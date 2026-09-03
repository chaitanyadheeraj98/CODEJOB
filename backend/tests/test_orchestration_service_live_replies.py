import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import EmailConversation, EmailReplyMessage, RecruiterEmail, UserSettings
from app.services.orchestration_service import OrchestrationService


class OrchestrationServiceLiveRepliesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_count_live_unread_replies_only_counts_sent_threads(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="recruiter@example.com",
                    subject="Role",
                    body="body",
                    sent_status="sent",
                    external_thread_id="t-sent",
                )
            )
            db.add(
                EmailConversation(
                    owner_id="default-owner",
                    root_recruiter_email_id=0,
                    external_thread_id="t-conv",
                )
            )
            db.commit()

            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_thread_ids=lambda: {"t-sent", "t-conv", "t-noise-newsletter"},
            )
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 2)

    def test_count_live_unread_replies_zero_when_unread_mail_is_not_a_sent_thread(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="recruiter@example.com",
                    subject="Role",
                    body="body",
                    sent_status="sent",
                    external_thread_id="t-sent",
                )
            )
            db.commit()

            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_thread_ids=lambda: {"t-noise-newsletter", "t-promo"},
            )
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 0)

    def test_count_live_unread_replies_returns_zero_when_dep_missing(self) -> None:
        with Session(self.engine) as db:
            deps = SimpleNamespace(owner_id="default-owner", list_unread_thread_ids=None)
            count = OrchestrationService(deps).count_live_unread_replies(db)

        self.assertEqual(count, 0)


    def test_capture_inbound_replies_still_captures_when_message_already_a_recruiter_email(self) -> None:
        """Reproduces the Sameer Bhatia bug: a reply's subject also matched the saved JD
        search, so it got imported as a RecruiterEmail candidate before the reply scan ran.
        The reply must still be captured - "already a RecruiterEmail" isn't "already a reply".
        """
        with Session(self.engine) as db:
            root = RecruiterEmail(
                owner_id="default-owner",
                sender="me",
                subject="Application",
                body="body",
                sent_status="sent",
                external_thread_id="t1",
                gmail_sent_id="sent-1",
            )
            db.add(root)
            db.flush()
            db.add(
                EmailConversation(
                    owner_id="default-owner",
                    root_recruiter_email_id=root.id,
                    external_thread_id="t1",
                    status="sent",
                    last_message_at=datetime.now(UTC),
                    unread_reply_count=0,
                )
            )
            # The bogus candidate row the JD-scan pipeline created for the same Gmail message.
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="Recruiter <r@example.com>",
                    subject="RE: Application",
                    body="Looking for locals only",
                    external_message_id="reply-1",
                    external_thread_id="t1",
                )
            )
            db.add(UserSettings(owner_id="default-owner", feature_reply_inbox_enabled=True, signature_email="me@example.com"))
            db.commit()

            reply_item = {
                "external_message_id": "reply-1",
                "external_thread_id": "t1",
                "external_rfc_message_id": None,
                "in_reply_to_header": "",
                "references_header": "",
                "sender": "Recruiter <r@example.com>",
                "body": "Looking for locals only",
                "snippet": "Looking for locals only",
                "gmail_received_at": datetime.now(UTC),
            }
            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_candidates_by_query=lambda *_a, **_k: [],
                list_thread_messages=lambda thread_id: [reply_item] if thread_id == "t1" else [],
            )
            user_settings = db.query(UserSettings).filter(UserSettings.owner_id == "default-owner").first()
            with patch("app.services.application_intelligence_service.correlate_reply_to_application") as correlate:
                matched_count, created_count = OrchestrationService(deps)._capture_inbound_replies(db, user_settings)
            correlate.assert_not_called()
            db.commit()

            self.assertEqual((matched_count, created_count), (1, 1))
            reply_row = db.query(EmailReplyMessage).filter(EmailReplyMessage.external_message_id == "reply-1").first()
            self.assertIsNotNone(reply_row)
            self.assertEqual(reply_row.direction, "inbound")
            conversation = db.query(EmailConversation).filter(EmailConversation.external_thread_id == "t1").first()
            self.assertEqual(conversation.status, "replied")

    def test_capture_inbound_replies_correlates_only_when_application_automation_is_enabled(self) -> None:
        with Session(self.engine) as db:
            root = RecruiterEmail(
                owner_id="default-owner",
                sender="me",
                subject="Application",
                body="body",
                sent_status="sent",
                external_thread_id="t2",
            )
            db.add(root)
            db.flush()
            db.add(
                EmailConversation(
                    owner_id="default-owner",
                    root_recruiter_email_id=root.id,
                    external_thread_id="t2",
                    last_message_at=datetime.now(UTC),
                )
            )
            db.add(
                UserSettings(
                    owner_id="default-owner",
                    feature_reply_inbox_enabled=True,
                    feature_application_automation_enabled=True,
                    signature_email="me@example.com",
                )
            )
            db.commit()
            reply_item = {
                "external_message_id": "reply-2",
                "external_thread_id": "t2",
                "external_rfc_message_id": None,
                "in_reply_to_header": "",
                "references_header": "",
                "sender": "Recruiter <r@example.com>",
                "body": "Let's schedule a call",
                "snippet": "Let's schedule a call",
                "gmail_received_at": datetime.now(UTC),
            }
            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_candidates_by_query=lambda *_a, **_k: [reply_item],
                list_thread_messages=None,
                mark_reply_processed=None,
                mark_message_processed=lambda _message_id: None,
            )
            user_settings = db.query(UserSettings).filter(UserSettings.owner_id == "default-owner").one()
            with patch("app.services.application_intelligence_service.correlate_reply_to_application") as correlate:
                matched_count, created_count = OrchestrationService(deps)._capture_inbound_replies(db, user_settings)

            self.assertEqual((matched_count, created_count), (1, 1))
            correlate.assert_called_once()

    def test_capture_inbound_replies_skips_original_root_message_reappearing_unread(self) -> None:
        """The app never marks Gmail messages read, so the original recruiter email that
        seeded a candidate keeps reappearing in the JD-scan on every run after we've already
        auto-applied to it. That message is not a reply and must not flip the conversation
        to "replied" or create a bogus inbound row.
        """
        with Session(self.engine) as db:
            root = RecruiterEmail(
                owner_id="default-owner",
                sender="Recruiter <r@example.com>",
                subject="Java Full Stack Developer",
                body="job post body",
                sent_status="sent",
                external_thread_id="t1",
                external_message_id="job-post-1",
                gmail_sent_id="sent-1",
            )
            db.add(root)
            db.flush()
            db.add(
                EmailConversation(
                    owner_id="default-owner",
                    root_recruiter_email_id=root.id,
                    external_thread_id="t1",
                    status="sent",
                    last_message_at=datetime.now(UTC),
                    unread_reply_count=0,
                )
            )
            db.add(UserSettings(owner_id="default-owner", feature_reply_inbox_enabled=True, signature_email="me@example.com"))
            db.commit()

            # Same message the JD-scan already imported as `root`, still unread, rescanned.
            root_item = {
                "external_message_id": "job-post-1",
                "external_thread_id": "t1",
                "external_rfc_message_id": None,
                "in_reply_to_header": "",
                "references_header": "",
                "sender": "Recruiter <r@example.com>",
                "body": "job post body",
                "snippet": "job post body",
                "gmail_received_at": datetime.now(UTC),
            }
            deps = SimpleNamespace(
                owner_id="default-owner",
                list_unread_candidates_by_query=lambda *_a, **_k: [],
                list_thread_messages=lambda thread_id: [root_item] if thread_id == "t1" else [],
            )
            user_settings = db.query(UserSettings).filter(UserSettings.owner_id == "default-owner").first()
            matched_count, created_count = OrchestrationService(deps)._capture_inbound_replies(db, user_settings)
            db.commit()

            self.assertEqual((matched_count, created_count), (1, 0))
            reply_row = db.query(EmailReplyMessage).filter(EmailReplyMessage.external_message_id == "job-post-1").first()
            self.assertIsNone(reply_row)
            conversation = db.query(EmailConversation).filter(EmailConversation.external_thread_id == "t1").first()
            self.assertEqual(conversation.status, "sent")
            self.assertEqual(conversation.unread_reply_count, 0)


if __name__ == "__main__":
    unittest.main()
