"""Registering a Gmail watch, and draining what it signals.

The invariant every test here circles is the cursor. Gmail coalesces
notifications and rate-limits them to one per mailbox per second, so the
history ID in a notification is routinely ahead of, behind, or equal to what
has actually been processed. `gmail_credentials.gmail_history_id` is the only
authoritative position, it moves only after a complete drain, and a failed
drain must leave it exactly where it was - because a cursor that moved past
unprocessed changes is a silent gap, not a retryable error.
"""

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from googleapiclient.errors import HttpError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as app_db
from app import gmail_client
from app.config import settings
from app.db import Base
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    GmailCredential,
    GmailLabel,
    RecruiterEmail,
    RecruiterWatch,
    TrackedThread,
    User,
    UserSettings,
)
from app.services import distributed_lock, gmail_pubsub_service as service

OWNER = "usr_push"
TOPIC = "projects/codejob-prod/topics/gmail-mailbox-events"


class _Tracker:
    """Stands in for `SessionLocal`, and counts sessions that are open now.

    The counter is the point. "No session is held across a network call" is
    otherwise an assertion nobody can make - a leak looks like working code
    until the connection pool runs out under load.
    """

    def __init__(self, maker):
        self._maker = maker
        self.open = 0
        self.peak_during_network = 0

    @contextmanager
    def begin(self):
        self.open += 1
        session = self._maker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self.open -= 1

    def __call__(self):
        return self._maker()


def _item(message_id: str, *, thread_id="t1", sender="recruiter@acme.example",
          labels=(), subject="Re: a role", in_reply_to=""):
    return {
        "external_message_id": message_id,
        "external_thread_id": thread_id,
        "sender": sender,
        "subject": subject,
        "body": "body text",
        "label_ids": list(labels),
        "in_reply_to_header": in_reply_to,
        "references_header": "",
        "to_header": "me@example.com",
        "cc_header": "",
        "received_at": datetime.now(UTC),
    }


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.maker = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.tracker = _Tracker(self.maker)
        self.db = self.maker()

        self._patches = [
            patch.object(app_db, "SessionLocal", self.tracker),
            patch.object(settings, "feature_gmail_pubsub_enabled", True),
            patch.object(settings, "gmail_pubsub_project_id", "codejob-prod"),
            patch.object(settings, "gmail_pubsub_topic_id", "gmail-mailbox-events"),
            patch.object(settings, "gmail_pubsub_subscription_id", "codejob-sub"),
            patch.object(distributed_lock, "hold", self._fake_lock),
        ]
        for item in self._patches:
            item.start()
        self.seed()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    @contextmanager
    def _fake_lock(name, *, owner_id, **kwargs):
        yield

    def seed(self, *, reply_inbox=True, disabled=False, cursor=None):
        self.db.add(User(owner_id=OWNER, email="me@example.com", display_name="Me",
                         disabled_at=datetime.now(UTC) if disabled else None))
        self.db.add(UserSettings(owner_id=OWNER, signature_email="me@example.com",
                                 feature_reply_inbox_enabled=reply_inbox))
        self.db.add(GmailCredential(owner_id=OWNER, google_email="me@example.com",
                                    access_token_encrypted="cipher",
                                    refresh_token_encrypted="cipher",
                                    gmail_history_id=cursor))
        self.db.commit()

    def row(self) -> GmailCredential:
        self.db.expire_all()
        return self.db.query(GmailCredential).filter(GmailCredential.owner_id == OWNER).one()

    def set_cursor(self, value: str, *, renewed_at=None):
        row = self.row()
        row.gmail_history_id = value
        row.gmail_watch_renewed_at = renewed_at
        self.db.commit()


class WatchRegistrationTests(_Base):
    def _watch(self, history_id="500", expires_in=timedelta(days=7)):
        return gmail_client.MailboxWatch(
            history_id=history_id, expiration_at=datetime.now(UTC) + expires_in
        )

    def test_a_first_registration_stores_the_baseline(self):
        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch()) as call:
            outcome = service.register_or_renew_watch(OWNER)

        self.assertTrue(outcome.registered)
        self.assertTrue(outcome.baseline, "the first registration owes a migration scan")
        call.assert_called_once_with(TOPIC)
        self.assertEqual(self.row().gmail_history_id, "500")

    def test_a_renewal_never_overwrites_the_processing_cursor(self):
        """The history ID in a watch response is the mailbox's position *now*.
        Adopting it on renewal would skip every change queued since the last
        drain, and skip it silently - nothing errors, the changes simply never
        arrive."""
        self.set_cursor("100")

        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch("900")):
            outcome = service.register_or_renew_watch(OWNER)

        self.assertTrue(outcome.registered)
        self.assertFalse(outcome.baseline)
        self.assertEqual(self.row().gmail_history_id, "100")

    def test_a_renewal_does_move_the_expiry(self):
        self.set_cursor("100")
        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch("900")):
            service.register_or_renew_watch(OWNER)

        row = self.row()
        self.assertIsNotNone(row.gmail_watch_renewed_at)
        self.assertGreater(row.gmail_watch_expiration_at, datetime.now(UTC) + timedelta(days=6))

    def test_the_watch_error_is_cleared_only_on_success(self):
        row = self.row()
        row.gmail_watch_last_error = "watch_unavailable"
        self.db.commit()

        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch()):
            service.register_or_renew_watch(OWNER)

        self.assertEqual(self.row().gmail_watch_last_error, "")

    def test_a_google_failure_records_a_code_not_the_exception(self):
        """A Google error carries response bodies, and an argument error about
        a topic carries the topic. Settings renders this column."""
        response = type("R", (), {"status": 403, "reason": "Forbidden"})()
        error = HttpError(response, b'{"error": {"message": "secret-topic-detail"}}')

        with patch.object(gmail_client, "watch_mailbox", side_effect=error):
            outcome = service.register_or_renew_watch(OWNER)

        self.assertFalse(outcome.registered)
        self.assertEqual(outcome.reason, "watch_permission_denied")
        stored = self.row().gmail_watch_last_error
        self.assertEqual(stored, "watch_permission_denied")
        self.assertNotIn("secret-topic-detail", stored)

    def test_a_failed_registration_leaves_the_cursor_alone(self):
        self.set_cursor("100")
        response = type("R", (), {"status": 503, "reason": "busy"})()

        with patch.object(gmail_client, "watch_mailbox", side_effect=HttpError(response, b"{}")):
            service.register_or_renew_watch(OWNER)

        self.assertEqual(self.row().gmail_history_id, "100")

    def test_it_refuses_when_push_is_switched_off(self):
        with patch.object(settings, "feature_gmail_pubsub_enabled", False):
            with patch.object(gmail_client, "watch_mailbox") as call:
                outcome = service.register_or_renew_watch(OWNER)

        self.assertEqual(outcome.reason, "push_disabled")
        call.assert_not_called()

    def test_it_refuses_when_the_topic_is_unnamed(self):
        with patch.object(settings, "gmail_pubsub_topic_id", ""):
            with patch.object(gmail_client, "watch_mailbox") as call:
                outcome = service.register_or_renew_watch(OWNER)

        self.assertEqual(outcome.reason, "not_configured")
        call.assert_not_called()

    def test_a_disabled_owner_gets_no_watch(self):
        row = self.db.query(User).filter(User.owner_id == OWNER).one()
        row.disabled_at = datetime.now(UTC)
        self.db.commit()

        with patch.object(gmail_client, "watch_mailbox") as call:
            outcome = service.register_or_renew_watch(OWNER)

        self.assertEqual(outcome.reason, "owner_disabled")
        call.assert_not_called()

    def test_a_mailbox_with_every_consumer_off_gets_no_watch(self):
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == OWNER).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        with patch.object(gmail_client, "watch_mailbox") as call:
            outcome = service.register_or_renew_watch(OWNER)

        self.assertEqual(outcome.reason, "inbox_features_off")
        call.assert_not_called()

    def test_a_revoked_credential_gets_no_watch(self):
        row = self.row()
        row.revoked_at = datetime.now(UTC)
        self.db.commit()

        with patch.object(gmail_client, "watch_mailbox") as call:
            outcome = service.register_or_renew_watch(OWNER)

        self.assertEqual(outcome.reason, "credential_revoked")
        call.assert_not_called()

    def test_a_first_registration_runs_one_bounded_scan(self):
        """The only planned migration scan: everything already in the mailbox
        predates the watch, so no notification will ever describe it."""
        scans = []
        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch()):
            service.register_or_renew_watch(OWNER, reconcile=scans.append)

        self.assertEqual(scans, [OWNER])

    def test_a_renewal_runs_no_scan(self):
        """Otherwise the feature re-scans every mailbox once a day, which is
        the thing it was built to stop doing."""
        self.set_cursor("100")
        scans = []

        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch("900")):
            service.register_or_renew_watch(OWNER, reconcile=scans.append)

        self.assertEqual(scans, [])

    def test_the_baseline_is_stored_before_the_migration_scan(self):
        """The opposite order to stale-cursor recovery, deliberately. Changes
        arriving *during* this scan stay in history after the baseline and are
        processed by their own notification. Storing the baseline afterwards
        would put them before the cursor, where nothing would fetch them."""
        seen = []

        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch("500")):
            service.register_or_renew_watch(
                OWNER, reconcile=lambda owner: seen.append(self.row().gmail_history_id)
            )

        self.assertEqual(seen, ["500"])

    def test_the_migration_scan_holds_the_history_lock(self):
        """So it cannot interleave with a notification arriving the moment the
        watch goes live."""
        held = []

        @contextmanager
        def record(name, *, owner_id, **kwargs):
            held.append((name, owner_id))
            yield

        with patch.object(distributed_lock, "hold", record), \
                patch.object(gmail_client, "watch_mailbox", return_value=self._watch()):
            service.register_or_renew_watch(OWNER, reconcile=lambda owner: None)

        self.assertEqual(held, [(distributed_lock.GMAIL_HISTORY, OWNER)])

    def test_a_failing_scan_does_not_undo_a_live_watch(self):
        """The watch is registered at Google either way. Reporting failure here
        would have the caller retry `users.watch`, and the retry would find a
        cursor already stored and skip the scan anyway."""
        def boom(owner):
            raise RuntimeError("scan exploded")

        with patch.object(gmail_client, "watch_mailbox", return_value=self._watch()):
            outcome = service.register_or_renew_watch(OWNER, reconcile=boom)

        self.assertTrue(outcome.registered)
        self.assertEqual(self.row().gmail_history_id, "500")

    def test_no_session_is_open_while_google_is_being_called(self):
        """A pool connection parked for an HTTP round trip, times every
        mailbox, is how a hundred-connection budget disappears."""
        observed = []

        def watch(topic):
            observed.append(self.tracker.open)
            return self._watch()

        with patch.object(gmail_client, "watch_mailbox", side_effect=watch):
            service.register_or_renew_watch(OWNER)

        self.assertEqual(observed, [0])


class StopTests(_Base):
    def test_stopping_clears_the_local_state(self):
        self.set_cursor("100")
        with patch.object(gmail_client, "stop_mailbox_watch") as call:
            self.assertTrue(service.stop_watch(OWNER))

        call.assert_called_once()
        row = self.row()
        self.assertIsNone(row.gmail_history_id)
        self.assertIsNone(row.gmail_watch_expiration_at)

    def test_the_state_is_cleared_even_when_google_refuses(self):
        """This runs immediately before the token is revoked. There is no
        second chance to ask, and a row claiming an active watch nobody is
        listening to is worse than one that admits it has none."""
        self.set_cursor("100")
        with patch.object(gmail_client, "stop_mailbox_watch", side_effect=RuntimeError("boom")):
            self.assertFalse(service.stop_watch(OWNER))

        self.assertIsNone(self.row().gmail_history_id)


class DueTests(_Base):
    def test_a_mailbox_with_no_cursor_is_due(self):
        self.assertEqual(service.owners_due_for_watch(self.db), [OWNER])

    def test_a_mailbox_renewed_an_hour_ago_is_not_due(self):
        self.set_cursor("100", renewed_at=datetime.now(UTC) - timedelta(hours=1))

        self.assertEqual(service.owners_due_for_watch(self.db), [])

    def test_a_mailbox_renewed_over_a_day_ago_is_due(self):
        self.set_cursor("100", renewed_at=datetime.now(UTC) - timedelta(hours=25))

        self.assertEqual(service.owners_due_for_watch(self.db), [OWNER])

    def test_the_renewal_window_is_well_inside_gmail_s_seven_days(self):
        """Renewing daily is what makes a failed renewal an alert rather than
        an outage. A window that crept toward seven days would leave no margin
        and no test would notice."""
        self.assertLessEqual(service.RENEWAL_INTERVAL, timedelta(hours=24))
        self.assertGreaterEqual(service.RENEWAL_INTERVAL, timedelta(hours=1))

    def test_an_ineligible_mailbox_is_never_due(self):
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == OWNER).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        self.assertEqual(service.owners_due_for_watch(self.db), [])

    def test_switching_the_reply_inbox_off_makes_a_watch_due_for_stopping(self):
        """How the checkbox stops delivery. Doing it in the Settings request
        would make saving wait on Google, and fail the save when Google is
        slow."""
        self.set_cursor("100")
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == OWNER).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        self.assertEqual(service.owners_due_for_stop(self.db), [OWNER])

    def test_a_healthy_mailbox_is_not_due_for_stopping(self):
        self.set_cursor("100")

        self.assertEqual(service.owners_due_for_stop(self.db), [])


class _DrainBase(_Base):
    def pages(self, *pages):
        """A `list_history` stand-in that walks the pages it was given."""
        calls = []

        def list_history(start, token=None):
            calls.append((start, token))
            return pages[len(calls) - 1]

        self.history_calls = calls
        return list_history

    def page(self, ids, *, next_token=None, history_id="140"):
        return gmail_client.HistoryPage(
            message_ids=tuple(ids), next_page_token=next_token, history_id=history_id
        )


class DrainTests(_DrainBase):
    def test_it_follows_every_page_before_advancing(self):
        self.set_cursor("100")
        fetched = []

        def fetch(ids):
            fetched.extend(ids)
            return []

        with patch.object(gmail_client, "list_history", self.pages(
            self.page(["m1"], next_token="p2", history_id="120"),
            self.page(["m2"], history_id="140"),
        )), patch.object(gmail_client, "get_candidates_by_message_ids", fetch):
            outcome = service.process_history(OWNER, "999")

        self.assertEqual(self.history_calls, [("100", None), ("100", "p2")])
        self.assertEqual(fetched, ["m1", "m2"])
        self.assertEqual(self.row().gmail_history_id, "140")
        self.assertTrue(outcome.cursor_advanced)

    def test_the_same_message_across_pages_is_fetched_once(self):
        self.set_cursor("100")
        fetched = []

        with patch.object(gmail_client, "list_history", self.pages(
            self.page(["m1"], next_token="p2"),
            self.page(["m1", "m2"]),
        )), patch.object(gmail_client, "get_candidates_by_message_ids",
                         lambda ids: fetched.extend(ids) or []):
            service.process_history(OWNER)

        self.assertEqual(fetched, ["m1", "m2"])

    def test_the_notification_history_id_is_never_used_as_the_cursor(self):
        """Gmail coalesces notifications, so this number is routinely ahead of
        what has been processed. Starting from it would skip the gap."""
        self.set_cursor("100")

        with patch.object(gmail_client, "list_history", self.pages(self.page([]))), \
                patch.object(gmail_client, "get_candidates_by_message_ids", lambda ids: []):
            service.process_history(OWNER, "999999")

        self.assertEqual(self.history_calls[0][0], "100")
        self.assertNotEqual(self.row().gmail_history_id, "999999")

    def test_an_out_of_order_notification_cannot_move_the_cursor_backwards(self):
        """Two notifications, the older arriving second. Both drain from the
        committed cursor, so the second is a no-op rather than a rewind."""
        self.set_cursor("100")

        with patch.object(gmail_client, "list_history", self.pages(
            self.page([], history_id="140"), self.page([], history_id="140"),
        )), patch.object(gmail_client, "get_candidates_by_message_ids", lambda ids: []):
            service.process_history(OWNER, "140")
            service.process_history(OWNER, "120")

        self.assertEqual(self.row().gmail_history_id, "140")

    def test_a_transient_failure_leaves_the_cursor_where_it_was(self):
        self.set_cursor("100")
        response = type("R", (), {"status": 503, "reason": "busy"})()

        with patch.object(gmail_client, "list_history", side_effect=HttpError(response, b"{}")):
            with self.assertRaises(HttpError):
                service.process_history(OWNER)

        self.assertEqual(self.row().gmail_history_id, "100")

    def test_a_mailbox_with_no_baseline_does_not_drain(self):
        with patch.object(gmail_client, "list_history") as call:
            outcome = service.process_history(OWNER, "500")

        self.assertEqual(outcome.reason, "no_cursor")
        call.assert_not_called()

    def test_a_busy_lock_is_not_an_error(self):
        """The holder's drain covers this notification's range too, because the
        range is defined by the cursor rather than by the notification."""
        self.set_cursor("100")

        @contextmanager
        def busy(name, *, owner_id, **kwargs):
            raise distributed_lock.LockBusy(name)
            yield

        with patch.object(distributed_lock, "hold", busy):
            with patch.object(gmail_client, "list_history") as call:
                outcome = service.process_history(OWNER)

        self.assertEqual(outcome.reason, "locked")
        call.assert_not_called()

    def test_the_drain_holds_the_per_owner_history_lock(self):
        self.set_cursor("100")
        held = []

        @contextmanager
        def record(name, *, owner_id, **kwargs):
            held.append((name, owner_id))
            yield

        with patch.object(distributed_lock, "hold", record), \
                patch.object(gmail_client, "list_history", self.pages(self.page([]))), \
                patch.object(gmail_client, "get_candidates_by_message_ids", lambda ids: []):
            service.process_history(OWNER)

        self.assertEqual(held, [(distributed_lock.GMAIL_HISTORY, OWNER)])

    def test_a_mailbox_with_every_consumer_off_ingests_nothing(self):
        self.set_cursor("100")
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == OWNER).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        with patch.object(gmail_client, "list_history") as call:
            outcome = service.process_history(OWNER)

        self.assertEqual(outcome.reason, "inbox_features_off")
        call.assert_not_called()

    def test_no_session_is_open_while_gmail_is_being_called(self):
        self.set_cursor("100")
        observed = []

        def list_history(start, token=None):
            observed.append(self.tracker.open)
            return self.page([])

        with patch.object(gmail_client, "list_history", list_history), \
                patch.object(gmail_client, "get_candidates_by_message_ids", lambda ids: []):
            service.process_history(OWNER)

        self.assertEqual(observed, [0])

    def test_no_session_is_open_while_messages_are_being_fetched(self):
        self.set_cursor("100")
        observed = []

        def fetch(ids):
            observed.append(self.tracker.open)
            return []

        with patch.object(gmail_client, "list_history", self.pages(self.page(["m1"]))), \
                patch.object(gmail_client, "get_candidates_by_message_ids", fetch):
            service.process_history(OWNER)

        self.assertEqual(observed, [0])


class RecoveryTests(_DrainBase):
    def test_a_dead_cursor_triggers_one_scan_and_a_fresh_baseline(self):
        self.set_cursor("100")
        scans = []
        fresh = gmail_client.MailboxWatch(
            history_id="777", expiration_at=datetime.now(UTC) + timedelta(days=7)
        )

        with patch.object(gmail_client, "list_history",
                          side_effect=gmail_client.StaleHistoryId("100")), \
                patch.object(gmail_client, "watch_mailbox", return_value=fresh):
            outcome = service.process_history(OWNER, reconcile=scans.append)

        self.assertTrue(outcome.recovered)
        self.assertEqual(scans, [OWNER], "exactly one bounded scan, not one per page")
        self.assertEqual(self.row().gmail_history_id, "777")

    def test_the_fresh_baseline_is_stored_only_after_the_scan_commits(self):
        """Order is the opposite of the obvious one. A baseline written first,
        followed by a crash, claims everything up to it was processed when
        nothing was. Storing it last can at worst repeat an idempotent scan."""
        self.set_cursor("100")
        seen = []
        fresh = gmail_client.MailboxWatch(history_id="777", expiration_at=None)

        with patch.object(gmail_client, "list_history",
                          side_effect=gmail_client.StaleHistoryId("100")), \
                patch.object(gmail_client, "watch_mailbox", return_value=fresh):
            service.process_history(OWNER, reconcile=lambda owner: seen.append(self.row().gmail_history_id))

        self.assertEqual(seen, ["100"], "the old cursor was still in place during the scan")

    def test_a_failed_re_registration_leaves_the_old_cursor(self):
        self.set_cursor("100")
        response = type("R", (), {"status": 503, "reason": "busy"})()
        scans = []

        with patch.object(gmail_client, "list_history",
                          side_effect=gmail_client.StaleHistoryId("100")), \
                patch.object(gmail_client, "watch_mailbox", side_effect=HttpError(response, b"{}")):
            outcome = service.process_history(OWNER, reconcile=scans.append)

        self.assertEqual(outcome.reason, "watch_unavailable")
        self.assertEqual(scans, [], "no scan without a baseline to anchor it")
        self.assertEqual(self.row().gmail_history_id, "100")


class CaptureTests(_Base):
    """The three reasons a message matters, applied without a Gmail query."""

    def _tracked_label(self, external_id="Label_7"):
        self.db.add(GmailLabel(owner_id=OWNER, external_label_id=external_id,
                               name="Recruiters", is_tracked=True))
        self.db.commit()
        return external_id

    def _capture(self, item, *, labels=(), watches=()):
        with self.tracker.begin() as db:
            return service.capture_message(
                db, OWNER, item,
                owner_email="me@example.com",
                tracked_label_ids=set(labels),
                watches=list(watches),
            )

    def test_a_message_under_a_tracked_label_is_captured(self):
        label = self._tracked_label()

        outcome = self._capture(_item("m1", labels=[label]), labels=[label])

        self.assertEqual(outcome, "label")
        thread = self.db.query(TrackedThread).one()
        self.assertEqual(thread.external_thread_id, "t1")

    def test_a_message_under_an_untracked_label_is_ignored(self):
        self._tracked_label()

        outcome = self._capture(_item("m1", labels=["Label_99"]), labels=["Label_7"])

        self.assertEqual(outcome, "")
        self.assertEqual(self.db.query(TrackedThread).count(), 0)

    def test_a_watch_matches_without_building_a_gmail_query(self):
        """The whole point of replacing the scans: the same recruiter-address
        rule applied to a message that arrived on its own."""
        self.db.add(RecruiterWatch(owner_id=OWNER, watch_type="address",
                                   value="recruiter@acme.example",
                                   source_thread_ids_json="[]"))
        self.db.commit()
        watches = self.db.query(RecruiterWatch).all()

        with patch.object(gmail_client, "list_candidates_by_query") as query:
            outcome = self._capture(_item("m1"), watches=watches)

        self.assertEqual(outcome, "watch")
        query.assert_not_called()

    def test_removing_the_last_tracked_label_untracks_the_thread(self):
        label = self._tracked_label()
        self._capture(_item("m1", labels=[label]), labels=[label])

        # The same message, refetched, now carrying no tracked label.
        self._capture(_item("m1", labels=["INBOX"]), labels=[label])

        self.db.expire_all()
        thread = self.db.query(TrackedThread).one()
        self.assertIsNotNone(thread.untracked_at)

    def test_a_thread_keeping_one_tracked_label_stays_tracked(self):
        first, second = self._tracked_label("Label_7"), self._tracked_label("Label_8")
        self._capture(_item("m1", labels=[first, second]), labels=[first, second])

        self._capture(_item("m1", labels=[second]), labels=[first, second])

        self.db.expire_all()
        thread = self.db.query(TrackedThread).one()
        self.assertIsNone(thread.untracked_at)

    def test_capturing_the_same_message_twice_creates_one_row(self):
        """At-least-once delivery is the contract. Redelivery has to be dull."""
        label = self._tracked_label()

        self._capture(_item("m1", labels=[label]), labels=[label])
        self._capture(_item("m1", labels=[label]), labels=[label])

        self.assertEqual(
            self.db.query(EmailReplyMessage).filter(
                EmailReplyMessage.external_message_id == "m1").count(),
            1,
        )


if __name__ == "__main__":
    unittest.main()


class ConsumerTests(_Base):
    """Two features consume Gmail change events, switched independently.

    Conflating them is how this went wrong in production: eligibility asked
    only about the Reply Inbox, so an account with label tracking on and the
    Reply Inbox off registered no watch - while Phase D had already stopped its
    scans. The scans went away and nothing replaced them, with nothing saying
    so. These tests exist because that shipped.
    """

    def _flags(self, *, replies: bool, labels: bool, global_labels: bool = True):
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == OWNER).one()
        row.feature_reply_inbox_enabled = replies
        row.feature_label_tracking_enabled = labels
        self.db.commit()
        return patch.object(settings, "feature_label_tracking_enabled", global_labels)

    def test_label_tracking_alone_is_enough_to_earn_a_watch(self):
        """The regression. Without this, its scans stop and nothing replaces
        them."""
        with self._flags(replies=False, labels=True):
            self.assertEqual(service.owners_due_for_watch(self.db), [OWNER])

    def test_the_reply_inbox_alone_is_enough_to_earn_a_watch(self):
        with self._flags(replies=True, labels=False):
            self.assertEqual(service.owners_due_for_watch(self.db), [OWNER])

    def test_neither_consumer_earns_nothing(self):
        with self._flags(replies=False, labels=False):
            self.assertEqual(service.owners_due_for_watch(self.db), [])

    def test_label_tracking_switched_off_globally_does_not_count(self):
        """`_sync_label_tracking` needs both halves of the flag. Eligibility
        matching it approximately would register watches for a feature the
        deployment has switched off."""
        with self._flags(replies=False, labels=True, global_labels=False):
            self.assertEqual(service.owners_due_for_watch(self.db), [])

    def test_consumers_reports_each_switch_separately(self):
        with self._flags(replies=True, labels=False):
            self.assertEqual(service.consumers(self.db, OWNER), (True, False))
        with self._flags(replies=False, labels=True):
            self.assertEqual(service.consumers(self.db, OWNER), (False, True))

    def test_a_label_only_mailbox_does_not_capture_replies(self):
        """A watch registered for one feature must not quietly do the other's
        work. The scan it replaces returned early on exactly this flag."""
        captured = []
        with self.tracker.begin() as db:
            with patch.object(service.email_inbox_service, "capture_inbound_reply",
                              side_effect=lambda *a, **k: captured.append(1) or (True, True)):
                service.capture_message(
                    db, OWNER, _item("m1"),
                    owner_email="me@example.com",
                    tracked_label_ids=set(), watches=[],
                    capture_replies=False,
                )

        self.assertEqual(captured, [])

    def test_a_reply_only_mailbox_still_captures_replies(self):
        captured = []
        with self.tracker.begin() as db:
            with patch.object(service.email_inbox_service, "capture_inbound_reply",
                              side_effect=lambda *a, **k: captured.append(1) or (True, False)):
                outcome = service.capture_message(
                    db, OWNER, _item("m1"),
                    owner_email="me@example.com",
                    tracked_label_ids=set(), watches=[],
                    capture_replies=True,
                )

        self.assertEqual(captured, [1])
        self.assertEqual(outcome, "reply")
