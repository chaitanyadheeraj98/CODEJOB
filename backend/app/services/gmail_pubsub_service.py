"""Gmail push delivery: keeping a watch alive, and draining what it signals.

A Pub/Sub notification carries `{emailAddress, historyId}` and nothing else. It
is a reason to go and look, not the thing that changed. Everything that decides
*what* changed lives here, between `gmail_client`'s three thin wrappers and the
per-message capture the scans already do.

Two rules run through the whole file.

**The cursor is the authoritative position, not the notification.** Gmail
coalesces notifications and rate-limits them to one per second per mailbox, so
the history ID in a notification is routinely ahead of, behind, or equal to
what has actually been processed. `gmail_history_id` on the credential row
moves only after every page of a drain and every message in it has been
handled. A failed drain leaves it exactly where it was, and the next
notification - or the next renewal - picks the same range up again.

**No session is held across a network call.** Reads happen in a short session
that closes before Gmail is contacted; writes happen in another one afterwards.
The alternative is a pool connection parked for the length of an HTTP round
trip, multiplied by every mailbox, which is how a hundred-connection budget
disappears.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from sqlalchemy.orm import Session

from app import gmail_client, tenancy
from app.config import settings
from app.db import session_scope
from app.models import GmailCredential, TrackedThread, UserSettings
from app.services import (
    account_service,
    distributed_lock,
    email_inbox_service,
    gmail_credential_service,
    gmail_label_service,
    label_tracking_service,
)

logger = logging.getLogger(__name__)

#: Gmail expires a watch seven days after it is registered. Renewing daily
#: leaves six days of margin, which is what makes a failed renewal an alert
#: rather than an outage. Not configurable - see `config.py`.
RENEWAL_INTERVAL = timedelta(hours=24)

#: One drain follows at most this many history pages. A mailbox that has been
#: unreachable for days can have more; the cursor does not move, so the next
#: notification continues from the same place rather than losing the remainder.
MAX_HISTORY_PAGES = 50

#: Message fetches per batch. `get_candidates_by_message_ids` issues one Gmail
#: call per id, so this is the unit of work between database sessions.
FETCH_BATCH = 50

DEFAULT_SIGNATURE_EMAIL = "unknown@example.com"

#: How long a notification may sit unprocessed before delivery reads as
#: delayed. A drain takes seconds; five minutes is slow enough that nothing
#: healthy trips it and short enough to notice within one coffee.
DELAYED_AFTER = timedelta(minutes=5)

#: The five words Settings can say about delivery. Anything outside this set is
#: a bug in the caller rather than a new state.
DELIVERY_DISABLED = "disabled"
DELIVERY_REGISTERING = "registering"
DELIVERY_ACTIVE = "active"
DELIVERY_DELAYED = "delayed"
DELIVERY_ERROR = "error"


class NotEligible(Exception):
    """This mailbox must not have a watch, and carries the safe reason why."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class WatchOutcome:
    registered: bool
    reason: str = ""
    #: True when this call established the first cursor for the mailbox, which
    #: is the only moment a migration scan is owed.
    baseline: bool = False


@dataclass(frozen=True)
class HistoryOutcome:
    processed: int = 0
    captured: int = 0
    reason: str = ""
    recovered: bool = False
    cursor_advanced: bool = False


# --- eligibility --------------------------------------------------------


def _ineligible(db: Session, owner_id: str) -> str:
    """Why this mailbox must not receive push delivery, or "" if it may.

    Checked before registering, before renewing and before ingesting, because
    the answer changes underneath all three: someone turns the Reply Inbox off,
    an administrator disables the account, Google revokes a token.
    """
    if account_service.is_owner_disabled(db, owner_id):
        return "owner_disabled"
    row = gmail_credential_service.get_row(db, owner_id)
    if row is None:
        return "no_credential"
    if row.revoked_at is not None:
        return "credential_revoked"
    if not (row.refresh_token_encrypted or row.access_token_encrypted):
        return "no_credential"
    user_settings = (
        db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    )
    if user_settings is None or not user_settings.feature_reply_inbox_enabled:
        return "reply_inbox_off"
    return ""


def push_delivery_active() -> bool:
    """Whether Gmail changes arrive by notification rather than by scanning.

    The single switch every scheduled scan asks before running. Deliberately
    also false when the topic is unnamed: a flag switched on against a
    half-configured project would stop the scans while nothing replaced them,
    which is the one outcome worse than either mode on its own.

    Not a per-user setting. Whether the mailbox is watched is per user; whether
    this deployment has push delivery at all is not.
    """
    return not _configured()


def eligibility_reason(db: Session, owner_id: str) -> str:
    """Public door to `_ineligible`, for the subscriber.

    The subscriber has to make the same judgement one step earlier - before
    enqueueing rather than before draining - and reaching into a private name
    to do it would make the one rule two.
    """
    return _ineligible(db, owner_id)


def _configured() -> str:
    if not settings.feature_gmail_pubsub_enabled:
        return "push_disabled"
    if not settings.gmail_pubsub_topic_name:
        return "not_configured"
    return ""


def _owner_email(db: Session, owner_id: str) -> str:
    user_settings = (
        db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    )
    return (getattr(user_settings, "signature_email", "") or "").strip() or DEFAULT_SIGNATURE_EMAIL


def _safe_reason(exc: Exception) -> str:
    """A code, never the exception text.

    A Google error carries response bodies, and an argument error about a topic
    carries the topic. Neither belongs in a column the Settings page renders.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status == 403:
        return "watch_permission_denied"
    if status == 404:
        return "watch_topic_not_found"
    if status == 400:
        return "watch_rejected"
    if status is not None and 500 <= int(status) < 600:
        return "watch_unavailable"
    return "watch_failed"


def _record_watch_error(owner_id: str, reason: str) -> None:
    with session_scope() as db:
        row = gmail_credential_service.get_row(db, owner_id)
        if row is not None:
            row.gmail_watch_last_error = reason


# --- watch lifecycle ----------------------------------------------------


def register_or_renew_watch(owner_id: str, *, reconcile=None) -> WatchOutcome:
    """Ask Gmail to keep publishing this mailbox's changes.

    The same call does first registration and daily renewal, because Gmail
    makes no distinction - `users.watch` is idempotent and simply resets the
    seven-day clock. The distinction that matters here is the cursor: a
    renewal must not touch one. The history ID in a watch response is the
    mailbox's position *now*, and adopting it would skip every change queued
    between the last drain and this renewal, silently.
    """
    blocked = _configured()
    if blocked:
        return WatchOutcome(False, blocked)

    with session_scope() as db:
        reason = _ineligible(db, owner_id)
        row = gmail_credential_service.get_row(db, owner_id)
        had_cursor = bool(row is not None and row.gmail_history_id)
    if reason:
        return WatchOutcome(False, reason)

    try:
        with tenancy.owner_scope(owner_id):
            watch = gmail_client.watch_mailbox(settings.gmail_pubsub_topic_name)
    except RefreshError:
        # The credential itself is the problem. The existing reconnect state
        # already says so; looping on it would just burn quota.
        _record_watch_error(owner_id, "reconnect_required")
        return WatchOutcome(False, "reconnect_required")
    except HttpError as exc:
        reason = _safe_reason(exc)
        logger.warning("gmail_watch_failed owner_id=%s reason=%s", owner_id, reason)
        _record_watch_error(owner_id, reason)
        return WatchOutcome(False, reason)

    with session_scope() as db:
        row = gmail_credential_service.get_row(db, owner_id)
        if row is None:
            return WatchOutcome(False, "no_credential")
        row.gmail_watch_expiration_at = watch.expiration_at
        row.gmail_watch_renewed_at = datetime.now(UTC)
        # Cleared only now, on success. Clearing it when the attempt starts
        # would leave Settings reporting health during the retry of a failure.
        row.gmail_watch_last_error = ""
        if not row.gmail_history_id:
            row.gmail_history_id = watch.history_id
        baseline = not had_cursor

    logger.info(
        "gmail_watch_registered owner_id=%s baseline=%s", owner_id, baseline
    )

    if baseline:
        # The one planned migration scan. Its order is deliberate and is the
        # opposite of recovery's: the baseline is stored *first*, so changes
        # arriving while the scan runs stay in Gmail's history after it and are
        # processed by their own notification. Storing it afterwards would put
        # them before the cursor, and nothing would ever fetch them.
        #
        # Under the history lock, so it cannot interleave with a notification
        # that arrives the moment the watch goes live.
        try:
            with distributed_lock.hold(distributed_lock.GMAIL_HISTORY, owner_id=owner_id):
                (reconcile or run_bounded_scan)(owner_id)
        except distributed_lock.LockBusy:
            logger.info("gmail_migration_scan_skipped owner_id=%s reason=locked", owner_id)
        except Exception:
            # A failed scan costs old mail that was never going to arrive by
            # notification anyway. It must not undo a watch that is now live.
            logger.exception("gmail_migration_scan_failed owner_id=%s", owner_id)

    return WatchOutcome(True, baseline=baseline)


def stop_watch(owner_id: str) -> bool:
    """Stop delivery and forget the watch. Returns whether Google confirmed.

    Deliberately best-effort at Google's end but unconditional here. This runs
    on disconnect and deactivation, immediately before the token is revoked -
    and once the token is gone there is no second chance to ask. Keeping local
    state because the remote call failed would leave a row claiming an active
    watch nothing is listening to.
    """
    stopped = False
    try:
        with tenancy.owner_scope(owner_id):
            gmail_client.stop_mailbox_watch()
        stopped = True
    except Exception as exc:
        logger.warning(
            "gmail_watch_stop_failed owner_id=%s error=%s", owner_id, type(exc).__name__
        )

    with session_scope() as db:
        row = gmail_credential_service.get_row(db, owner_id)
        if row is not None:
            gmail_credential_service.clear_watch_state(row)
    return stopped


def owners_due_for_watch(db: Session) -> list[str]:
    """Mailboxes that should have a watch registered or renewed right now.

    Cheap SQL over one small table, run every minute. Each eligible mailbox
    makes at most one `users.watch` call a day; the rest of the ticks answer
    from these two timestamps.
    """
    due: list[str] = []
    cutoff = datetime.now(UTC) - RENEWAL_INTERVAL
    for row in db.query(GmailCredential).filter(GmailCredential.revoked_at.is_(None)):
        if _ineligible(db, row.owner_id):
            continue
        renewed = row.gmail_watch_renewed_at
        if not row.gmail_history_id or renewed is None or renewed <= cutoff:
            due.append(row.owner_id)
    return due


def owners_due_for_stop(db: Session) -> list[str]:
    """Mailboxes holding watch state that are no longer allowed to.

    This is how turning the Reply Inbox off stops delivery. Doing it in the
    Settings request instead would make saving a checkbox wait on a Google
    round trip, and fail the save when Google is slow.
    """
    return [
        row.owner_id
        for row in db.query(GmailCredential)
        if (row.gmail_history_id or row.gmail_watch_expiration_at)
        and _ineligible(db, row.owner_id)
    ]


# --- capture ------------------------------------------------------------


def capture_message(
    db: Session,
    owner_id: str,
    item,
    *,
    owner_email: str,
    tracked_label_ids: set[str],
    watches: list,
) -> str:
    """Apply the existing capture rules to one fetched message.

    The three paths are the three reasons a message matters, checked in that
    order of strength: it is a reply to something this account sent, it is
    under a label the user tracks, or it is from an address a tracked thread
    made a watch of. A message can satisfy more than one - the reply path wins
    the return value, and the label path still records its thread, because
    which conversation a message belongs to is not the same question as why it
    was interesting.

    Returns "reply", "new_reply", "label", "watch" or "".
    """
    outcome = ""

    matched, created = email_inbox_service.capture_inbound_reply(
        db, owner_id=owner_id, item=item, owner_email=owner_email
    )
    if matched:
        outcome = "new_reply" if created else "reply"

    current_labels = set(item.get("label_ids") or [])
    for label_id in sorted(current_labels & tracked_label_ids):
        label_tracking_service.attach_labeled_message(
            db, owner_id, item=item, label_external_id=label_id, owner_email=owner_email
        )
        outcome = outcome or "label"

    _reconcile_thread_labels(db, owner_id, item, tracked_label_ids)

    if not outcome and watches:
        watch = label_tracking_service.match_watch(item, watches, owner_email)
        if watch is not None and label_tracking_service.attach_watch_message(
            db, owner_id, item=item, watch=watch, owner_email=owner_email
        ):
            outcome = "watch"

    return outcome


def _reconcile_thread_labels(db: Session, owner_id: str, item, tracked_label_ids: set[str]) -> None:
    """Bring a tracked thread back in line with the message's current labels.

    This is what a removal looks like from here. `list_history` does not report
    removals as a separate kind of thing on purpose: a refetched message
    carries the labels it has *now*, so adding and removing converge on the
    same question - which tracked labels still apply.

    A thread that has lost its last tracked label is marked untracked and the
    watches it produced are released through the existing reconciliation rule,
    the same one the scan uses.
    """
    thread_id = (item.get("external_thread_id") or "").strip()
    if not thread_id:
        return
    thread = (
        db.query(TrackedThread)
        .filter(TrackedThread.owner_id == owner_id, TrackedThread.external_thread_id == thread_id)
        .first()
    )
    if thread is None or thread.untracked_at is not None:
        return
    still_tracked = sorted(set(item.get("label_ids") or []) & tracked_label_ids)
    if json.loads(thread.label_external_ids_json) == still_tracked:
        return
    thread.label_external_ids_json = json.dumps(still_tracked)
    if not still_tracked:
        thread.untracked_at = datetime.now(UTC)
        logger.info("label_thread_untracked owner_id=%s", owner_id)
    db.flush()
    label_tracking_service.reconcile_watches(db, owner_id)


# --- history ------------------------------------------------------------


def process_history(owner_id: str, notification_history_id: str = "", *, reconcile=None) -> HistoryOutcome:
    """Drain everything Gmail has recorded since the committed cursor.

    Holds the per-owner history lock for the whole drain. Two workers on the
    same mailbox would otherwise fetch overlapping ranges and race to write the
    cursor, and the loser's write can move it backwards - which is not a
    duplicate, it is a gap the next drain will never revisit.

    `notification_history_id` is accepted and deliberately unused as a
    position. It is logged-adjacent context: the reason this ran, not where to
    start from.
    """
    blocked = _configured()
    if blocked:
        return HistoryOutcome(reason=blocked)

    with session_scope() as db:
        reason = _ineligible(db, owner_id)
        row = gmail_credential_service.get_row(db, owner_id)
        cursor = (row.gmail_history_id or "") if row is not None else ""
    if reason:
        return HistoryOutcome(reason=reason)
    if not cursor:
        # No baseline yet; the watch-management loop owes this mailbox a
        # registration. Draining from nowhere is not a thing that can be done.
        return HistoryOutcome(reason="no_cursor")

    try:
        with distributed_lock.hold(distributed_lock.GMAIL_HISTORY, owner_id=owner_id):
            return _drain(owner_id, cursor, reconcile=reconcile)
    except distributed_lock.LockBusy:
        # Another worker holds the same mailbox. Its drain covers this
        # notification's range too, because the range is defined by the cursor
        # rather than by the notification.
        return HistoryOutcome(reason="locked")


def _drain(owner_id: str, cursor: str, *, reconcile=None) -> HistoryOutcome:
    message_ids: list[str] = []
    terminal = cursor
    page_token: str | None = None
    try:
        with tenancy.owner_scope(owner_id):
            for _ in range(MAX_HISTORY_PAGES):
                page = gmail_client.list_history(cursor, page_token)
                message_ids.extend(page.message_ids)
                terminal = page.history_id or terminal
                page_token = page.next_page_token
                if not page_token:
                    break
    except gmail_client.StaleHistoryId:
        return _recover(owner_id, reconcile=reconcile)

    # Deduplicated across pages as well as within one: a long drain sees the
    # same thread repeatedly, and each duplicate is a full message fetch.
    unique_ids = list(dict.fromkeys(message_ids))

    processed = 0
    captured = 0
    for start in range(0, len(unique_ids), FETCH_BATCH):
        batch = unique_ids[start : start + FETCH_BATCH]
        with tenancy.owner_scope(owner_id):
            # Messages deleted between the history record and this fetch come
            # back 404 and are skipped by the client, which is correct: the
            # change still happened, there is simply nothing left to capture.
            items = gmail_client.get_candidates_by_message_ids(batch)
        processed += len(items)
        captured += _capture_batch(owner_id, items)

    with session_scope() as db:
        row = gmail_credential_service.get_row(db, owner_id)
        if row is not None:
            # Only now. Every page followed, every message handled.
            row.gmail_history_id = terminal or cursor
            row.gmail_last_event_processed_at = datetime.now(UTC)

    logger.info(
        "gmail_history_drained owner_id=%s messages=%s captured=%s",
        owner_id, processed, captured,
    )
    return HistoryOutcome(
        processed=processed, captured=captured, cursor_advanced=terminal != cursor
    )


def _capture_batch(owner_id: str, items: list) -> int:
    """Capture one fetched batch. No network happens inside this session."""
    captured = 0
    new_reply_ids: list[str] = []
    with session_scope() as db:
        owner_email = _owner_email(db, owner_id)
        tracked_label_ids = {
            label.external_label_id
            for label in gmail_label_service.list_labels(db, owner_id, tracked_only=True)
        }
        watches = label_tracking_service.active_watches(db, owner_id)
        for item in items:
            try:
                with db.begin_nested():
                    outcome = capture_message(
                        db, owner_id, item,
                        owner_email=owner_email,
                        tracked_label_ids=tracked_label_ids,
                        watches=watches,
                    )
            except Exception:
                # One unparseable message must not cost the rest of the batch,
                # and the savepoint is what makes that true rather than hopeful:
                # without it the session stays poisoned after the first failure.
                logger.exception("gmail_event_capture_failed owner_id=%s", owner_id)
                continue
            if outcome:
                captured += 1
            if outcome == "new_reply":
                new_reply_ids.append(item["external_message_id"])

    # After the commit, never inside it. Both of these read the reply rows as
    # committed facts, and running them in the same transaction would have them
    # acting on a reply that a later failure rolls back.
    if new_reply_ids:
        _after_new_replies(owner_id, new_reply_ids)
    return captured


def _after_new_replies(owner_id: str, external_message_ids: list[str]) -> None:
    """Application correlation and notifications, for genuinely new replies.

    Guarded by "genuinely new" rather than "matched": a redelivered
    notification re-matches the same message every time, and notifying someone
    twice about one reply is the visible half of that mistake.
    """
    from app.models import EmailReplyMessage

    with session_scope() as db:
        user_settings = (
            db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
        )
        if user_settings is not None and user_settings.feature_application_automation_enabled:
            from app.services import application_intelligence_service

            rows = (
                db.query(EmailReplyMessage.id)
                .filter(
                    EmailReplyMessage.owner_id == owner_id,
                    EmailReplyMessage.external_message_id.in_(external_message_ids),
                )
                .all()
            )
            for (reply_id,) in rows:
                try:
                    application_intelligence_service.correlate_reply_to_application(
                        db, owner_id=owner_id, reply_message_id=reply_id
                    )
                except Exception:
                    logger.exception(
                        "application_reply_correlation_failed reply_message_id=%s", reply_id
                    )

    try:
        from app.services.proactive_notification_service import generate_reply_notifications

        with session_scope() as db:
            with tenancy.owner_scope(owner_id):
                generate_reply_notifications(db)
    except Exception:
        logger.exception("proactive_notification_generation_failed owner_id=%s", owner_id)


# --- stale cursor -------------------------------------------------------


def _recover(owner_id: str, *, reconcile=None) -> HistoryOutcome:
    """Gmail no longer has history from our cursor. Re-baseline, once.

    Order matters and is the opposite of the obvious one. The fresh baseline is
    captured first but stored **last**, after the recovery scan has committed:
    a baseline written before the scan, followed by a crash, would leave a
    cursor that claims everything up to it was processed when nothing was.
    Storing it afterwards can at worst repeat a scan, which is idempotent.

    Runs inside the per-owner lock already held by `process_history`.
    """
    logger.warning("gmail_history_stale owner_id=%s", owner_id)
    try:
        with tenancy.owner_scope(owner_id):
            watch = gmail_client.watch_mailbox(settings.gmail_pubsub_topic_name)
    except (HttpError, RefreshError) as exc:
        reason = "reconnect_required" if isinstance(exc, RefreshError) else _safe_reason(exc)
        _record_watch_error(owner_id, reason)
        return HistoryOutcome(reason=reason)

    (reconcile or run_bounded_scan)(owner_id)

    with session_scope() as db:
        row = gmail_credential_service.get_row(db, owner_id)
        if row is not None:
            row.gmail_history_id = watch.history_id
            row.gmail_watch_expiration_at = watch.expiration_at
            row.gmail_watch_renewed_at = datetime.now(UTC)
            row.gmail_last_event_processed_at = datetime.now(UTC)
    return HistoryOutcome(reason="recovered", recovered=True, cursor_advanced=True)


def run_bounded_scan(owner_id: str) -> None:
    """The bounded scans this feature exists to stop running on a schedule.

    Named for what it is rather than for one of its callers. There are exactly
    two - first registration and stale-cursor recovery - because both are the same situation: a gap in history that no
    notification will ever describe. Everything else goes through the event
    path.
    """
    from app import main

    with session_scope() as db:
        with tenancy.owner_scope(owner_id):
            service = main._get_orchestration_service()
            user_settings = main._get_settings(db)
            service.reconcile_inbox_once(db, user_settings)


# --- status -------------------------------------------------------------


def delivery_state(status, *, consumer_online: bool, eligible: bool) -> str:
    """One word for "is the Reply Inbox actually receiving anything".

    Ordered by what a person can do about it, not by severity. A configuration
    error outranks an expired watch because the watch cannot be registered
    until the configuration is fixed; a missing consumer outranks a healthy
    watch because a watch nobody listens to delivers nothing.

    Takes a `GmailConnectionStatus` rather than a session: everything it needs
    is already derived, and a status helper that queries is a status helper
    that gets called in a loop.
    """
    if not push_delivery_active() or not eligible or not status.connected:
        return DELIVERY_DISABLED
    if status.watch_error:
        return DELIVERY_ERROR
    if not status.watch_active:
        # No watch yet, or it lapsed. Both read as "starting": the minute loop
        # registers one either way, and "expired" is a distinction the person
        # reading this cannot act on.
        return DELIVERY_REGISTERING
    if not consumer_online:
        return DELIVERY_DELAYED
    pending = status.last_notification_at
    processed = status.last_event_processed_at
    if pending is not None and datetime.now(UTC) - pending > DELAYED_AFTER:
        if processed is None or processed < pending:
            return DELIVERY_DELAYED
    return DELIVERY_ACTIVE
