"""The dedicated process that consumes Gmail's Pub/Sub notifications.

It does two things, both deliberately small.

It **pulls notifications and enqueues work**. The callback validates a payload,
resolves a mailbox address to an owner, and puts a job on `gmail_event`. It
never touches Gmail and never captures a message, because a callback that does
real work stops acknowledging while it does it, and Pub/Sub responds to that by
redelivering everything.

It **keeps watches alive**. Once a minute it asks which mailboxes are due for
registration or renewal, under a leader lease so a second copy of this process
does not double-register. Gmail expires a watch after seven days; nothing else
in the system would notice it lapse.

Deliberately absent: any HTTP endpoint. This is a pull subscriber precisely so
that no publicly reachable route has to exist for Google to deliver to.

Run as `python -m app.gmail_pubsub_subscriber`.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope
from rq import Retry

from app.jobs.queues import GMAIL_EVENT_QUEUE, get_queue, get_redis_connection
from app.models import GmailCredential
from app.services import distributed_lock, gmail_pubsub_service

logger = logging.getLogger(__name__)

#: Presence of this key is what the Settings page means by "the consumer is
#: online". A stored watch proves Gmail was told where to publish; it proves
#: nothing about anyone listening, which is exactly the failure this catches.
HEARTBEAT_KEY = "gmail_pubsub:subscriber:heartbeat"
HEARTBEAT_TTL_SECONDS = 120
HEARTBEAT_INTERVAL_SECONDS = 30

#: How often due watches are checked. A minute, because the cost is one indexed
#: query over a table with one row per connected account, and the benefit is
#: that turning the Reply Inbox on starts delivery while the user is still
#: looking at the page.
WATCH_SWEEP_INTERVAL_SECONDS = 60

#: The watch sweep is a singleton across processes, not per owner. Pub/Sub
#: load-balances a subscription across every subscriber, so a second copy of
#: this service is a supported accident - and two of them registering watches
#: would double every mailbox's daily quota for no benefit.
WATCH_LEADER_LOCK = "gmail_watch_renewer"
LEADER_SCOPE = "global"

#: Bounded so a backlog cannot be pulled into memory faster than the worker
#: drains it. The expected load is a handful of messages a minute.
MAX_OUTSTANDING_MESSAGES = 100


@dataclass(frozen=True)
class Notification:
    """The entire Gmail push payload. There is nothing else in it."""

    email_address: str
    history_id: str


def parse_notification(data: bytes) -> Notification | None:
    """Validate one payload, or return None for anything unusable.

    Not base64-decoded here. The Pub/Sub client library has already done that
    by the time a callback sees `message.data`; decoding again would fail on
    every valid message. Written down because the wire format *is* base64 and
    the obvious "fix" is to add a decode.

    `historyId` arrives as a JSON number or a string depending on nothing in
    particular, so both are accepted and normalised to the decimal string the
    rest of the system treats as opaque.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    address = str(payload.get("emailAddress") or "").strip().lower()
    history_id = str(payload.get("historyId") or "").strip()
    if not address or not history_id or not history_id.isdigit():
        return None
    return Notification(email_address=address, history_id=history_id)


def resolve_owner(db: Session, email_address: str) -> str:
    """Map a mailbox address to the owner that consented to it.

    The only use of the address in the payload, and the only direction this can
    go: address -> stored credential -> owner. An owner id never arrives in a
    notification, so a forged or misrouted payload can at worst name a mailbox
    that is not connected here, which resolves to nothing.

    Matches on the indexed `google_email`, which 0076 indexed for exactly this.
    A revoked credential deliberately does not match: the token is gone, so
    there is nothing to drain with.
    """
    row = (
        db.query(GmailCredential)
        .filter(
            GmailCredential.google_email == email_address,
            GmailCredential.revoked_at.is_(None),
        )
        .first()
    )
    return row.owner_id if row is not None else ""


def accept(notification: Notification) -> str:
    """Record the notification and enqueue the drain. Returns a safe outcome.

    Returns "" when the message should be acknowledged and dropped, a reason
    string otherwise. Raising is reserved for "this could not be delivered
    *yet*" - Redis or the database being unavailable - because that is the only
    case where redelivery helps.
    """
    with session_scope() as db:
        owner_id = resolve_owner(db, notification.email_address)
        if not owner_id:
            return "unknown_mailbox"
        ineligible = gmail_pubsub_service.eligibility_reason(db, owner_id)
        if ineligible:
            return ineligible
        row = (
            db.query(GmailCredential).filter(GmailCredential.owner_id == owner_id).first()
        )
        if row is not None:
            # The payload itself is never stored - only that one arrived, and
            # when. "Notifications arriving but nothing processed" is the shape
            # of a stuck worker, and it needs both halves to be visible.
            row.gmail_last_notification_at = datetime.now(UTC)

    get_queue(GMAIL_EVENT_QUEUE).enqueue(
        "app.jobs.tasks.run_gmail_history_job",
        owner_id=owner_id,
        notification_history_id=notification.history_id,
        job_timeout=600,
        # Retried in the worker rather than by nacking the notification, so a
        # transient Gmail 429 does not hold a Pub/Sub message open for minutes.
        # Safe to repeat: the drain starts from the committed cursor, which a
        # failure leaves untouched. Backed off far enough to outlast a rate
        # limit rather than argue with one.
        retry=Retry(max=3, interval=[30, 120, 300]),
    )
    return ""


def handle_message(message) -> None:
    """The Pub/Sub callback. Acknowledge exactly what cannot be retried.

    A poison message - malformed, or for a mailbox nobody here has connected -
    is acknowledged. Redelivering it forever cannot make it valid, and an
    unacknowledged backlog is what eventually stops the valid ones arriving.

    Everything logged here is a count or a code. Never the address, never the
    payload: an `emailAddress` in a log line is the one piece of personal data
    this process handles.
    """
    notification = parse_notification(getattr(message, "data", b"") or b"")
    if notification is None:
        logger.warning("gmail_pubsub_malformed")
        message.ack()
        return

    try:
        reason = accept(notification)
    except Exception as exc:
        # Redis or Postgres unavailable. Nack so Pub/Sub redelivers; the
        # subscription retains messages for seven days, which is far longer
        # than either outage should last.
        logger.warning("gmail_pubsub_deferred error=%s", type(exc).__name__)
        message.nack()
        return

    if reason:
        logger.info("gmail_pubsub_ignored reason=%s", reason)
    message.ack()


# --- watch management ---------------------------------------------------


def sweep_watches() -> tuple[int, int]:
    """Register or renew what is due, and stop what is no longer allowed.

    Returns (registered, stopped). Under a leader lease, so scaling this
    service is safe rather than merely unlikely.
    """
    try:
        with distributed_lock.hold(WATCH_LEADER_LOCK, owner_id=LEADER_SCOPE):
            with session_scope() as db:
                due = gmail_pubsub_service.owners_due_for_watch(db)
                stopping = gmail_pubsub_service.owners_due_for_stop(db)

            registered = 0
            for owner_id in due:
                if gmail_pubsub_service.register_or_renew_watch(owner_id).registered:
                    registered += 1
            for owner_id in stopping:
                gmail_pubsub_service.stop_watch(owner_id)
            return registered, len(stopping)
    except distributed_lock.LockBusy:
        return 0, 0


def sweep_pending_drains() -> int:
    """Re-enqueue a drain for any mailbox whose last notification went unread.

    A notification is acked as soon as it is enqueued, so a job that declines
    to run - most ordinarily because the first-registration migration scan holds
    the per-owner lock - leaves nothing for Pub/Sub to redeliver. Without this,
    those changes wait for the next notification to arrive on its own.

    The job id is per **owner**, not per notification, and that distinction is
    the whole point: it makes a repeat tick replace the pending catch-up rather
    than stack another one behind it, while still never deduplicating the real
    notifications, which legitimately coalesce into one history range.
    """
    try:
        with distributed_lock.hold(WATCH_LEADER_LOCK, owner_id=LEADER_SCOPE):
            with session_scope() as db:
                waiting = gmail_pubsub_service.owners_awaiting_drain(db)
            for owner_id in waiting:
                get_queue(GMAIL_EVENT_QUEUE).enqueue(
                    "app.jobs.tasks.run_gmail_history_job",
                    owner_id=owner_id,
                    notification_history_id="",
                    job_timeout=600,
                    # Dashes, not a colon: RQ rejects a job id containing anything
                    # outside letters, numbers, underscores and dashes.
                    job_id=f"gmail-catchup-{owner_id}",
                    retry=Retry(max=3, interval=[30, 120, 300]),
                )
            if waiting:
                logger.info("gmail_catchup_enqueued count=%s", len(waiting))
            return len(waiting)
    except distributed_lock.LockBusy:
        return 0


def write_heartbeat(connection=None) -> None:
    """Say the subscriber is alive, with a TTL short enough to expire fast."""
    (connection or get_redis_connection()).set(
        HEARTBEAT_KEY, datetime.now(UTC).isoformat(), ex=HEARTBEAT_TTL_SECONDS
    )


def subscriber_is_online(connection=None) -> bool:
    try:
        return bool((connection or get_redis_connection()).exists(HEARTBEAT_KEY))
    except Exception:
        # Redis being unreachable is not evidence the subscriber is running.
        return False


def _background_loop(stop: threading.Event) -> None:
    """Heartbeat every 30s, sweep watches every minute, until asked to stop."""
    ticks = 0
    while not stop.is_set():
        try:
            write_heartbeat()
        except Exception as exc:
            logger.warning("gmail_pubsub_heartbeat_failed error=%s", type(exc).__name__)
        if ticks % max(1, WATCH_SWEEP_INTERVAL_SECONDS // HEARTBEAT_INTERVAL_SECONDS) == 0:
            try:
                registered, stopped = sweep_watches()
                if registered or stopped:
                    logger.info(
                        "gmail_watch_sweep registered=%s stopped=%s", registered, stopped
                    )
            except Exception:
                logger.exception("gmail_watch_sweep_failed")
            # After the watch sweep, not before: a registration that just
            # happened is the commonest reason a drain is owed, and running
            # this first would miss it by one tick.
            try:
                sweep_pending_drains()
            except Exception:
                logger.exception("gmail_catchup_sweep_failed")
        ticks += 1
        stop.wait(HEARTBEAT_INTERVAL_SECONDS)


def run() -> int:
    """Start streaming pull and block. Exits loudly on a configuration fault.

    The Google client is imported here rather than at module scope so that the
    parsing, routing and acknowledgement decisions above stay importable and
    testable without it - and so that no test needs a Google credential to
    exercise them.
    """
    logging.basicConfig(level=logging.INFO)

    if not settings.feature_gmail_pubsub_enabled:
        logger.info("gmail_pubsub_disabled")
        return 0
    subscription = settings.gmail_pubsub_subscription_name
    if not subscription:
        # Fatal, not degraded. A subscriber that starts and consumes nothing is
        # indistinguishable from a quiet mailbox.
        logger.error("gmail_pubsub_unconfigured")
        return 2

    from google.cloud import pubsub_v1

    client = pubsub_v1.SubscriberClient()
    flow_control = pubsub_v1.types.FlowControl(max_messages=MAX_OUTSTANDING_MESSAGES)

    stop = threading.Event()
    background = threading.Thread(
        target=_background_loop, args=(stop,), name="gmail-pubsub-background", daemon=True
    )
    background.start()

    future = client.subscribe(subscription, callback=handle_message, flow_control=flow_control)
    logger.info("gmail_pubsub_started")

    def shutdown(*_):
        stop.set()
        future.cancel()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, shutdown)

    try:
        future.result()
    except Exception:
        logger.exception("gmail_pubsub_stream_failed")
        return 1
    finally:
        stop.set()
        client.close()
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(run())
