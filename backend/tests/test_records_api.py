import os
import unittest
import uuid
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import (
    AppTSApplication,
    AppTSApplicationInterview,
    CandidateRecord,
    EmailConversation,
    EmailReplyMessage,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)


class RecordsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with Session(self.engine) as db:
            db.add(UserSettings(
                owner_id=main.settings.owner_id,
                feature_applications_enabled=True,
                feature_resume_tracking_enabled=True,
            ))
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _gmail_graph(self, db: Session) -> tuple[str, int, int]:
        owner_id = main.settings.owner_id
        sent_at = datetime(2026, 8, 1, 12, tzinfo=UTC)
        record = CandidateRecord(id=str(uuid.uuid4()), owner_id=owner_id, origin_type="gmail")
        resume = ResumeAsset(
            owner_id=owner_id,
            file_path="missing-record-test.pdf",
            file_name="record-test.pdf",
            sha256="a" * 64,
            version=1,
        )
        contact = PremiumNumberContact(
            owner_id=owner_id,
            normalized_phone_number="12145550149",
            display_phone_number="+1 214 555 0149",
            is_recruiter=True,
            recruiter_name="Record Recruiter",
            recruiter_email="record@example.com",
        )
        db.add_all([record, resume, contact])
        db.flush()
        email = RecruiterEmail(
            owner_id=owner_id,
            sender="Record Recruiter <record@example.com>",
            subject="Record API role",
            body="Java role",
            state="approved_sent",
            decision="Qualified",
            source="gmail",
            external_message_id=f"record-{uuid.uuid4()}",
            external_thread_id=f"thread-{uuid.uuid4()}",
            recipient_email="record@example.com",
            cc_email="employer@example.com",
            sent_status="sent",
            sent_at=sent_at,
            record_id=record.id,
        )
        db.add(email)
        db.flush()
        opportunity = RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=contact.id,
            source_email_id=email.id,
            gmail_message_id=email.external_message_id or "record-message",
            source_type="gmail",
            email_subject=email.subject,
            email_sender=email.sender,
            job_title="Java Developer",
            resume_asset_id=resume.id,
            record_id=record.id,
        )
        db.add(opportunity)
        db.flush()
        lineage = OpportunityLineage(
            id=str(uuid.uuid4()),
            owner_id=owner_id,
            origin_type="gmail",
            recruiter_opportunity_id=opportunity.id,
            current_status="active",
        )
        db.add(lineage)
        db.flush()
        record.internal_lineage_id = lineage.id
        db.add(OpportunityLifecycleEvent(
            owner_id=owner_id,
            lineage_id=lineage.id,
            event_type="promoted",
            related_record_type="RecruiterOpportunity",
            related_record_id=opportunity.id,
        ))
        application = AppTSApplication(
            owner_id=owner_id,
            resume_asset_id=resume.id,
            resume_version_snapshot=resume.version,
            resume_file_name_snapshot=resume.file_name,
            resume_sha256_snapshot=resume.sha256,
            recruiter_opportunity_id=opportunity.id,
            recruiter_contact_id=contact.id,
            source_recruiter_email_id=email.id,
            resume_submission_status="submitted",
            status="interview_1",
        )
        db.add(application)
        db.flush()
        db.add(AppTSApplicationInterview(
            owner_id=owner_id,
            application_id=application.id,
            round_type="interview_1",
            result="scheduled",
        ))
        conversation = EmailConversation(
            owner_id=owner_id,
            root_recruiter_email_id=email.id,
            external_thread_id=email.external_thread_id or "thread",
            last_message_at=sent_at + timedelta(days=2),
        )
        db.add(conversation)
        db.flush()
        db.add(EmailReplyMessage(
            owner_id=owner_id,
            conversation_id=conversation.id,
            external_message_id=f"reply-{uuid.uuid4()}",
            direction="inbound",
            received_at=sent_at + timedelta(days=2),
        ))
        db.commit()
        return record.id, application.id, resume.id

    def test_unknown_malformed_and_foreign_records_share_404(self) -> None:
        with Session(self.engine) as db:
            foreign = CandidateRecord(id=str(uuid.uuid4()), owner_id="foreign-owner", origin_type="gmail")
            db.add(foreign)
            db.commit()
            foreign_id = foreign.id
        for record_id in (str(uuid.uuid4()), "not-a-record-id", foreign_id):
            response = self.client.get(f"/records/{record_id}")
            self.assertEqual(response.status_code, 404, response.text)
            self.assertEqual(response.json()["detail"], "Record not found")

    def test_gmail_record_returns_graph_and_reply_outcomes(self) -> None:
        with Session(self.engine) as db:
            record_id, application_id, _ = self._gmail_graph(db)
        response = self.client.get(f"/records/{record_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["source"]["type"], "gmail")
        self.assertIsNotNone(payload["recruiter_opportunity"])
        self.assertEqual([row["id"] for row in payload["applications"]], [application_id])
        self.assertEqual(payload["outcomes"]["inbound_reply_count"], 1)
        self.assertEqual(payload["outcomes"]["days_to_first_reply"], 2.0)
        self.assertTrue(payload["outcomes"]["interviewed"])
        self.assertEqual(payload["lineage"]["event_count"], 1)

    def test_nvoids_record_without_lineage_still_returns_source_and_outcomes(self) -> None:
        with Session(self.engine) as db:
            owner_id = main.settings.owner_id
            record = CandidateRecord(id=str(uuid.uuid4()), owner_id=owner_id, origin_type="nvoids")
            feed = ExternalFeedSource(owner_id=owner_id)
            db.add_all([record, feed])
            db.flush()
            external = ExternalOpportunity(
                owner_id=owner_id,
                feed_source_id=feed.id,
                external_post_id=f"post-{uuid.uuid4()}",
                source_url="https://nvoids.example/job",
                role="Python Developer",
                recruiter_email="nvoids@example.com",
                dedupe_hash=str(uuid.uuid4()),
                bridge_status="pending",
                record_id=record.id,
            )
            db.add(external)
            db.commit()
            record_id = record.id
            external_id = external.id
        payload = self.client.get(f"/records/{record_id}").json()
        self.assertEqual(payload["source"]["external_opportunity_id"], external_id)
        self.assertIsNone(payload["lineage"])
        self.assertIsNone(payload["recruiter_opportunity"])
        self.assertFalse(payload["outcomes"]["sent"])
        self.assertEqual(payload["outcomes"]["current_status"], "pending")

    def test_disabled_features_return_empty_application_sections(self) -> None:
        with Session(self.engine) as db:
            record_id, _, _ = self._gmail_graph(db)
            user_settings = db.query(UserSettings).filter_by(owner_id=main.settings.owner_id).one()
            user_settings.feature_applications_enabled = False
            user_settings.feature_resume_tracking_enabled = False
            db.commit()
        response = self.client.get(f"/records/{record_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertFalse(payload["applications_enabled"])
        self.assertFalse(payload["resume_tracking_enabled"])
        self.assertEqual(payload["applications"], [])
        self.assertEqual(payload["legacy_applications"], [])


if __name__ == "__main__":
    unittest.main()
