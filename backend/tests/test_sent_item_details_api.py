import os
import unittest
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import RecruiterEmail


class SentItemDetailsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_gmail_sent_details_returns_safe_defaults_and_sent_link(self) -> None:
        with Session(self.engine) as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Recruiter Name <recruiter@example.com>",
                subject="Java role",
                body="Need Java and Spring Boot",
                role="Java Developer",
                location="Remote",
                salary_text="",
                skills_text="Java, Spring Boot",
                score=88,
                decision="Qualified",
                state="approved_sent",
                draft_reply="Hi",
                approval_status="approved",
                sent_status="sent",
                source="gmail",
                external_message_id="msg-123",
                external_thread_id="thread-123",
                recipient_email="recruiter@example.com",
                cc_email="manager@example.com",
                routing_status="safe",
                routing_confidence=0.9,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                resume_file_name="resume.pdf",
                ats_score=84.0,
                ats_summary="ATS hybrid score 84/100",
                ats_breakdown_json='{"matched_raw_skills":["Java"],"missing_raw_skills":["Spring Boot"]}',
                parser_details_json='{"skills_audit":{"known":["Java"],"unknown":["Spring Boot"]}}',
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-xyz",
                sent_attachment_file_names_json='["cover-letter.pdf"]',
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.commit()
            email_id = row.id

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["email_id"], email_id)
        self.assertEqual(payload["source_label"], "Gmail")
        self.assertTrue(payload["requirement_received_link"].endswith("/#all/msg-123"))
        self.assertTrue(payload["sent_gmail_message_link"].endswith("/#all/sent-xyz"))
        self.assertEqual(payload["resume_variant_sent"], "resume.pdf")
        self.assertEqual(payload["attached_files"], ["cover-letter.pdf"])
        self.assertIsNone(payload["company"])
        self.assertIsNone(payload["end_client"])
        self.assertIsNone(payload["vendor"])
        self.assertEqual(payload["mandatory_skills"], ["Java", "Spring Boot"])
        self.assertEqual(payload["missing_skills"], ["Spring Boot"])
        self.assertEqual(payload["recruiter_name"], "Recruiter Name")
        self.assertEqual(payload["recruiter_email"], "recruiter@example.com")
        self.assertEqual(payload["recruiter_phone"], None)

    def test_nvoids_sent_details_resolve_external_opportunity_fields(self) -> None:
        with Session(self.engine) as db:
            source = ExternalFeedSource(owner_id=main.settings.owner_id, source_type="nvoids", base_url="https://nvoids.com")
            db.add(source)
            db.flush()
            db.add(
                ExternalOpportunity(
                    owner_id=main.settings.owner_id,
                    feed_source_id=source.id,
                    source_type="nvoids",
                    external_post_id="3385623",
                    source_url="https://nvoids.com/job_details.jsp?id=3385623",
                    recruiter_email="nvoids@example.com",
                    recruiter_phone="+1 214 555 0199",
                    recruiter_name="Nvoids Recruiter",
                    company="Acme Corp",
                    role="Senior Java Developer",
                    location="Dallas, TX",
                    raw_body="Role: Senior Java Developer",
                    raw_html="<html></html>",
                    dedupe_hash="hash-3385623",
                    parse_confidence=0.8,
                )
            )
            db.flush()
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="nvoids@example.com",
                subject="Senior Java Developer",
                body="Vendor: Example Vendor\nExperience Required: 8+ years",
                role="Senior Java Developer",
                location="Dallas, TX",
                salary_text="$70/hr",
                skills_text="Java, Spring Boot",
                score=90,
                decision="Qualified",
                state="approved_sent",
                draft_reply="Hi",
                approval_status="approved",
                sent_status="sent",
                source="nvoids",
                external_message_id="nvoids:3385623",
                external_thread_id="https://nvoids.com/job_details.jsp?id=3385623",
                recipient_email="nvoids@example.com",
                cc_email="employer@example.com",
                routing_status="safe",
                routing_confidence=0.95,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                resume_file_name="resume.pdf",
                ats_score=91.0,
                ats_summary="ATS hybrid score 91/100",
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-nvoids-1",
                sent_attachment_file_names_json='["portfolio.zip"]',
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.commit()
            email_id = row.id

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["source_label"], "Nvoids")
        self.assertEqual(payload["requirement_received_link"], "https://nvoids.com/job_details.jsp?id=3385623")
        self.assertEqual(payload["company"], "Acme Corp")
        self.assertEqual(payload["recruiter_name"], "Nvoids Recruiter")
        self.assertEqual(payload["recruiter_email"], "nvoids@example.com")
        self.assertEqual(payload["recruiter_phone"], "+1 214 555 0199")
        self.assertEqual(payload["vendor"], "Example Vendor")
        self.assertEqual(payload["experience_required"], "8+ years")
        self.assertEqual(payload["attached_files"], ["portfolio.zip"])


if __name__ == "__main__":
    unittest.main()
