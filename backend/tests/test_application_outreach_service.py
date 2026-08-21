import json
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    ApplicationEvent,
    AttachmentAsset,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)
from app.services import application_outreach_service, application_service


class ApplicationOutreachServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _application(db: Session, *, recruiter_email: str = "recruiter@example.com"):
        resume = ResumeAsset(
            owner_id="owner",
            file_path="locked-resume.pdf",
            file_name="locked-resume.pdf",
            mime_type="application/pdf",
            sha256="a" * 64,
            version=2,
            is_current=False,
        )
        current_resume = ResumeAsset(
            owner_id="owner",
            file_path="current-resume.pdf",
            file_name="current-resume.pdf",
            mime_type="application/pdf",
            sha256="b" * 64,
            version=3,
            is_current=True,
        )
        recruiter = PremiumNumberContact(
            owner_id="owner",
            normalized_phone_number="12145551001",
            display_phone_number="+1 214 555 1001",
            is_recruiter=True,
            recruiter_name="Priya Patel",
            recruiter_email=recruiter_email,
            company="ABC Staffing",
        )
        db.add_all([resume, current_resume, recruiter])
        db.flush()
        opportunity = RecruiterOpportunity(
            owner_id="owner",
            recruiter_number_id=recruiter.id,
            gmail_message_id="outreach-opportunity",
            job_title="Senior Java Developer",
            end_client="Bank X",
        )
        db.add(opportunity)
        db.flush()
        application = application_service.create_application(
            db,
            owner_id="owner",
            resume_asset_id=resume.id,
            recruiter_opportunity_id=opportunity.id,
        )
        db.flush()
        return application, resume, current_resume, recruiter

    @staticmethod
    def _link_email(db: Session, application) -> RecruiterEmail:
        email = RecruiterEmail(
            owner_id="owner",
            sender="Priya <recruiter@example.com>",
            subject="Senior Java Developer",
            body="Role details",
            recipient_email="thread-recipient@example.com",
            cc_email="employer@example.com",
            external_message_id="outreach-source-message",
            external_thread_id="thread-123",
            source="gmail",
        )
        db.add(email)
        db.flush()
        application_service.append_event(
            db,
            application,
            event_type="email_linked",
            linked_recruiter_email_id=email.id,
        )
        db.flush()
        return email

    def test_resolve_recipient_prefers_linked_thread_then_falls_back_and_validates(self) -> None:
        with Session(self.engine) as db:
            application, _, _, recruiter = self._application(db)
            fallback = application_outreach_service.resolve_recipient(db, application)
            self.assertEqual(fallback.to, "recruiter@example.com")
            self.assertIsNone(fallback.thread_id)

            email = self._link_email(db, application)
            linked = application_outreach_service.resolve_recipient(db, application)
            self.assertEqual(linked.to, "thread-recipient@example.com")
            self.assertEqual(linked.cc, "employer@example.com")
            self.assertEqual(linked.thread_id, "thread-123")
            self.assertEqual(linked.source_recruiter_email_id, email.id)

            email.source = "nvoids"
            self.assertIsNone(application_outreach_service.resolve_recipient(db, application).thread_id)
            email.recipient_email = ""
            recruiter.recruiter_email = ""
            db.flush()
            with self.assertRaises(application_service.ApplicationValidationError):
                application_outreach_service.resolve_recipient(db, application)

    def test_ai_disabled_uses_locked_resume_and_never_calls_deepseek(self) -> None:
        with Session(self.engine) as db:
            application, resume, current_resume, _ = self._application(db)
            settings = UserSettings(
                owner_id="owner",
                feature_application_outreach_drafts_enabled=False,
                signature_name="Chaithanya",
                signature_phone="555-0100",
                signature_email="candidate@example.com",
            )
            with (
                patch(
                    "app.services.application_outreach_service.extract_resume_context",
                    return_value="7+ years Java",
                ) as extract,
                patch("app.services.application_outreach_service.deepseek_chat_completion") as completion,
            ):
                result = application_outreach_service.build_application_draft(
                    db,
                    application,
                    message_kind="followup",
                    user_settings=settings,
                    model_name="deepseek-fast",
                )
            self.assertEqual(result.source, "ai_disabled")
            self.assertEqual(result.resume_file_name, resume.file_name)
            self.assertIn("Chaithanya", result.body)
            self.assertIn("candidate@example.com", result.body)
            extract.assert_called_once_with(resume.file_path, resume.file_name)
            self.assertNotEqual(resume.id, current_resume.id)
            completion.assert_not_called()

    def test_ai_draft_extracts_subject_caps_claims_and_falls_back_on_failure(self) -> None:
        with Session(self.engine) as db:
            application, _, _, _ = self._application(db)
            settings = UserSettings(
                owner_id="owner",
                feature_application_outreach_drafts_enabled=True,
                signature_name="Chaithanya",
            )
            with (
                patch(
                    "app.services.application_outreach_service.extract_resume_context",
                    return_value="7+ years of Java experience",
                ),
                patch(
                    "app.services.application_outreach_service.deepseek_chat_completion",
                    return_value=(
                        "Subject: Checking in on Senior Java Developer\n\n"
                        "Hi Priya,\n\nI have 15 years of Java experience.\n\nBest regards,\nChaithanya"
                    ),
                ),
            ):
                result = application_outreach_service.build_application_draft(
                    db,
                    application,
                    message_kind="followup",
                    user_settings=settings,
                    model_name="deepseek-fast",
                )
            self.assertEqual(result.source, "deepseek")
            self.assertEqual(result.subject, "Checking in on Senior Java Developer")
            self.assertNotIn("Subject:", result.body)
            self.assertIn("7+ years", result.body)
            self.assertNotIn("15 years", result.body)

            with (
                patch(
                    "app.services.application_outreach_service.extract_resume_context",
                    return_value="7+ years of Java experience",
                ),
                patch(
                    "app.services.application_outreach_service.deepseek_chat_completion",
                    side_effect=RuntimeError("provider down"),
                ),
            ):
                fallback = application_outreach_service.build_application_draft(
                    db,
                    application,
                    message_kind="submission_to_recruiter",
                    user_settings=settings,
                    model_name="deepseek-fast",
                )
            self.assertEqual(fallback.source, "rules_only")
            self.assertEqual(fallback.ai_error, "provider down")
            self.assertIn("resume attached", fallback.body)

    def test_send_reply_records_evidence_without_changing_status(self) -> None:
        with Session(self.engine) as db:
            application, resume, _, _ = self._application(db)
            email = self._link_email(db, application)
            original_status = application.status
            attachment = AttachmentAsset(
                owner_id="owner",
                file_path="rtr-proof.pdf",
                file_name="rtr-proof.pdf",
                mime_type="application/pdf",
                sha256="c" * 64,
                file_size=10,
            )
            db.add(attachment)
            db.flush()
            with patch(
                "app.services.application_outreach_service.gmail_client.send_reply_with_attachment",
                return_value="gmail-sent-1",
            ) as send_reply:
                row, message_id = application_outreach_service.send_application_message(
                    db,
                    application,
                    to="thread-recipient@example.com",
                    cc="employer@example.com",
                    subject="Following up",
                    body="Any update?",
                    thread_id="thread-123",
                    message_kind="followup",
                    include_resume=True,
                    attachment_asset_ids=[attachment.id],
                )
            db.flush()

            self.assertEqual(message_id, "gmail-sent-1")
            self.assertEqual(row.status, original_status)
            self.assertEqual(row.follow_up_count, 1)
            self.assertIsNotNone(row.last_contact_at)
            sent = db.query(ApplicationEvent).filter_by(event_type="outreach_sent").one()
            metadata = json.loads(sent.metadata_json)
            self.assertEqual(sent.linked_recruiter_email_id, email.id)
            self.assertEqual(metadata["gmail_message_id"], "gmail-sent-1")
            self.assertEqual(metadata["attachment_names"], [resume.file_name, attachment.file_name])
            self.assertEqual(send_reply.call_args.kwargs["thread_id"], "thread-123")
            self.assertEqual(len(send_reply.call_args.kwargs["attachments"]), 2)

    def test_send_new_email_and_rejects_cross_owner_attachment_before_gmail(self) -> None:
        with Session(self.engine) as db:
            application, _, _, _ = self._application(db)
            other_attachment = AttachmentAsset(
                owner_id="other-owner",
                file_path="other.pdf",
                file_name="other.pdf",
                sha256="d" * 64,
                file_size=10,
            )
            db.add(other_attachment)
            db.flush()
            with (
                patch("app.services.application_outreach_service.gmail_client.send_reply_with_attachment") as reply,
                patch("app.services.application_outreach_service.gmail_client.send_new_email_with_attachment") as new,
            ):
                with self.assertRaises(application_service.ApplicationReferenceNotFoundError):
                    application_outreach_service.send_application_message(
                        db,
                        application,
                        to="recruiter@example.com",
                        cc=None,
                        subject="Resume",
                        body="Please see attached.",
                        thread_id=None,
                        message_kind="submission_to_recruiter",
                        include_resume=False,
                        attachment_asset_ids=[other_attachment.id],
                    )
                reply.assert_not_called()
                new.assert_not_called()

            with patch(
                "app.services.application_outreach_service.gmail_client.send_new_email_with_attachment",
                return_value="gmail-new-1",
            ) as new:
                _, message_id = application_outreach_service.send_application_message(
                    db,
                    application,
                    to="recruiter@example.com",
                    cc=None,
                    subject="Resume",
                    body="Please see attached.",
                    thread_id=None,
                    message_kind="submission_to_recruiter",
                    include_resume=False,
                    attachment_asset_ids=[],
                )
            self.assertEqual(message_id, "gmail-new-1")
            new.assert_called_once()


if __name__ == "__main__":
    unittest.main()
