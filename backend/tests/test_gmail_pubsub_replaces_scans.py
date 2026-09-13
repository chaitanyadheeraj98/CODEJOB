"""Under push delivery, the recurring Gmail scans must not run at all.

The claim this phase makes is negative - "these no longer happen" - and a
negative is the easy kind of claim to believe wrongly. So the Gmail helpers are
not mocked to return nothing here; they are replaced with functions that
**raise**. A scan that still runs cannot pass quietly, and a test that passes
because a scan happened to find nothing cannot exist.

Both directions are asserted. Rollback is one flag, and a flag that turned off
the scans permanently would not be a rollback.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import EmailConversation, RecruiterEmail, UserSettings
from app.services import gmail_pubsub_service
from app.services.orchestration_service import OrchestrationService

OWNER = "usr_scan"


class _Boom(Exception):
    """Raised by any Gmail helper a scan would have called."""


def _explode(*args, **kwargs):
    raise _Boom("a Gmail scan ran under push delivery")


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.user_settings = UserSettings(
            owner_id=OWNER, signature_email="me@example.com",
            feature_reply_inbox_enabled=True, feature_label_tracking_enabled=True,
            enabled=True,
        )
        self.db.add(self.user_settings)
        self.db.commit()

        # Every Gmail-touching dependency a scan could reach, armed to raise.
        self.deps = SimpleNamespace(
            owner_id=OWNER,
            get_settings=lambda db: self.user_settings,
            list_unread_candidates_by_query=_explode,
            list_thread_messages=_explode,
            list_unread_thread_ids=_explode,
            list_candidates_by_label_ids=_explode,
            list_thread_ids_by_label=_explode,
            list_candidates_by_query=_explode,
            list_gmail_labels=_explode,
            mark_reply_processed=None,
            mark_message_processed=_explode,
            is_gmail_configured=lambda: True,
        )
        self.service = OrchestrationService(self.deps)

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def push_on(self):
        return patch.multiple(
            settings,
            feature_gmail_pubsub_enabled=True,
            gmail_pubsub_project_id="codejob-prod",
            gmail_pubsub_topic_id="gmail-mailbox-events",
        )

    def push_off(self):
        return patch.object(settings, "feature_gmail_pubsub_enabled", False)


class SwitchTests(_Base):
    def test_push_is_active_only_when_configured(self):
        with self.push_on():
            self.assertTrue(gmail_pubsub_service.push_delivery_active())

    def test_a_flag_without_a_topic_does_not_stop_the_scans(self):
        """The one outcome worse than either mode: scans switched off with
        nothing replacing them, because the project was half-configured."""
        with patch.multiple(settings, feature_gmail_pubsub_enabled=True,
                            gmail_pubsub_project_id="", gmail_pubsub_topic_id=""):
            self.assertFalse(gmail_pubsub_service.push_delivery_active())
            self.assertTrue(self.service._scans_are_scheduled())

    def test_the_scans_are_scheduled_while_push_is_off(self):
        with self.push_off():
            self.assertTrue(self.service._scans_are_scheduled())


class RefreshTests(_Base):
    def test_the_refresh_icon_makes_no_gmail_call_under_push(self):
        with self.push_on():
            result = self.service.refresh_inbox_replies(self.db)

        self.assertEqual(result, [])

    def test_the_refresh_icon_still_scans_when_push_is_off(self):
        """Rollback has to be one flag. If this passed under both, the flag
        would not be a rollback - it would be a one-way removal.

        The scan's own error handling turns `_Boom` into the 502 it has always
        raised when Gmail rejects a reply scan, so that is what reaching Gmail
        looks like from out here.
        """
        from fastapi import HTTPException

        with self.push_off():
            with self.assertRaises(HTTPException) as caught:
                self.service.refresh_inbox_replies(self.db)

        self.assertEqual(caught.exception.status_code, 502)


class LiveCountTests(_Base):
    def test_the_navigation_count_makes_no_gmail_call_under_push(self):
        self.db.add(EmailConversation(
            owner_id=OWNER, external_thread_id="t1", unread_reply_count=3,
            recruiter_email_snapshot="r@example.com", subject_snapshot="Re: role",
        ))
        self.db.commit()

        with self.push_on():
            self.assertEqual(self.service.count_live_unread_replies(self.db), 3)

    def test_it_counts_only_this_owner(self):
        self.db.add(EmailConversation(
            owner_id=OWNER, external_thread_id="t1", unread_reply_count=2,
            recruiter_email_snapshot="r@example.com", subject_snapshot="a",
        ))
        self.db.add(EmailConversation(
            owner_id="usr_other", external_thread_id="t2", unread_reply_count=9,
            recruiter_email_snapshot="r@example.com", subject_snapshot="b",
        ))
        self.db.commit()

        with self.push_on():
            self.assertEqual(self.service.count_live_unread_replies(self.db), 2)

    def test_an_empty_inbox_counts_zero_rather_than_none(self):
        """`SUM` over no rows is NULL, and NULL reaching a response model is a
        500 on a page that should simply say nothing is waiting."""
        with self.push_on():
            self.assertEqual(self.service.count_live_unread_replies(self.db), 0)

    def test_it_still_asks_gmail_when_push_is_off(self):
        # A sent thread has to exist, or the Gmail query is skipped for its own
        # reasons and this would pass without proving anything.
        self.db.add(RecruiterEmail(
            owner_id=OWNER, sender="r@example.com", subject="role",
            body="b", external_thread_id="t1", sent_status="sent",
        ))
        self.db.commit()

        with self.push_off():
            with self.assertRaises(_Boom):
                self.service.count_live_unread_replies(self.db)


class RecoveryStillScansTests(_Base):
    def test_the_recovery_entry_point_scans_even_under_push(self):
        """The exception that makes the rule work. First registration and
        stale-cursor recovery are gaps no notification will ever describe, so
        they need exactly the behaviour everything else is giving up."""
        with self.push_on():
            with self.assertRaises(_Boom):
                self.service.reconcile_inbox_once(self.db, self.user_settings)


if __name__ == "__main__":
    unittest.main()


class LifecycleOrderTests(unittest.TestCase):
    """Stopping the watch has to happen while the token still works.

    `users.stop` authenticates with the credential being revoked. Do it
    afterwards and the call fails, leaving Gmail publishing notifications for a
    mailbox this installation can no longer read - which is not a leak, but it
    is quota spent on messages that will be acked and dropped forever.
    """

    def test_deactivation_stops_the_watch_before_revoking_the_token(self):
        from app.services import account_service

        order = []
        with patch.object(account_service, "_stop_jobs", return_value=0), \
                patch.object(account_service.auth_service, "revoke_all_sessions", return_value=0), \
                patch("app.services.gmail_pubsub_service.stop_watch",
                      side_effect=lambda owner: order.append("stop_watch")), \
                patch.object(account_service, "_revoke_google_access",
                             side_effect=lambda db, owner: order.append("revoke")):
            db = _FakeSession()
            user = SimpleNamespace(
                id=1, owner_id=OWNER, disabled_at=None, deletion_requested_at=None,
            )
            account_service.deactivate(db, user)

        self.assertEqual(order, ["stop_watch", "revoke"])

    def test_a_google_failure_does_not_block_deactivation(self):
        """The user asked to leave. The watch expires on its own inside seven
        days regardless, so refusing here would be the wrong trade twice."""
        from app.services import account_service

        with patch.object(account_service, "_stop_jobs", return_value=0), \
                patch.object(account_service.auth_service, "revoke_all_sessions", return_value=0), \
                patch("app.services.gmail_pubsub_service.stop_watch",
                      side_effect=RuntimeError("google is down")), \
                patch.object(account_service, "_revoke_google_access"):
            db = _FakeSession()
            user = SimpleNamespace(
                id=1, owner_id=OWNER, disabled_at=None, deletion_requested_at=None,
            )
            result = account_service.deactivate(db, user)

        self.assertIsNotNone(result.deactivated_at)

    def test_disconnect_stops_the_watch_before_clearing_the_ciphertext(self):
        """`mark_revoked` clears the token `users.stop` needs, so source order
        is the whole guarantee.

        Compared over executable statements, not raw text. The first attempt
        searched the source string and matched the *comment* above the call,
        which sits before it and inverted the result - the same way a compose
        check elsewhere in this repo once matched a comment naming the flag it
        was meant to be checking for.
        """
        import ast
        import inspect

        from app import main

        tree = ast.parse(inspect.getsource(main.disconnect_gmail).lstrip())
        # Sorted by line number: `ast.walk` is breadth-first and makes no
        # promise about source order, which is the only thing being asserted.
        calls = [
            name for _, name in sorted(
                (node.lineno, node.func.attr)
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            )
        ]

        self.assertIn("stop_watch", calls)
        self.assertIn("mark_revoked", calls)
        self.assertLess(calls.index("stop_watch"), calls.index("mark_revoked"))


class _FakeSession:
    """Just enough Session for `deactivate` - it only queries to delete."""

    def query(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def delete(self, *args, **kwargs):
        return 0

    def flush(self):
        return None
