import os
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.models import ExternalOpportunity
from app.models import RecruiterEmail, RecruiterNumber, RecruiterOpportunity


class ColdCallApiTests(unittest.TestCase):
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

    def _seed_recruiter(self, db: Session) -> RecruiterNumber:
        recruiter = RecruiterNumber(
            owner_id=main.settings.owner_id,
            normalized_phone_number="+12145550000",
            display_phone_number="+1 214 555 0000",
            recruiter_name="Sam",
            company="Acme",
            designation="Recruiter",
            recruiter_email="sam@acme.com",
            first_detected_email_id=None,
        )
        db.add(recruiter)
        db.commit()
        db.refresh(recruiter)
        return recruiter

    def _seed_opportunity(self) -> int:
        with Session(self.engine) as db:
            recruiter = self._seed_recruiter(db)
            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="msg-1",
                email_subject="Java Lead role",
                email_sender="sam@acme.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Java Lead",
                client="Acme",
                location="Austin, TX",
                work_mode="Hybrid",
                visa_restrictions="",
                extracted_skills="java, spring boot",
                evidence="Need telecom experience",
                status="New",
                notes="",
            )
            db.add(opportunity)
            db.commit()
            db.refresh(opportunity)
            return opportunity.id

    def _seed_gmail_backed_opportunity(self) -> int:
        with Session(self.engine) as db:
            recruiter = self._seed_recruiter(db)
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="sam@acme.com",
                subject="Java Lead role",
                body="Required Skills: Java, Spring Boot, Kubernetes",
                role="Java Lead",
                location="Austin, TX",
                salary_text="",
                skills_text="Java, Spring Boot, Kubernetes",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=email.id,
                gmail_message_id="msg-1",
                email_subject="Java Lead role",
                email_sender="sam@acme.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Java Lead",
                client="Acme",
                location="Austin, TX",
                work_mode="Hybrid",
                visa_restrictions="",
                extracted_skills="java, spring boot",
                evidence="Need telecom experience",
                status="New",
                notes="",
            )
            db.add(opportunity)
            db.commit()
            db.refresh(opportunity)
            return opportunity.id

    def _seed_nvoids_backed_opportunity(self) -> int:
        with Session(self.engine) as db:
            recruiter = self._seed_recruiter(db)
            external = ExternalOpportunity(
                owner_id=main.settings.owner_id,
                feed_source_id=1,
                source_type="nvoids",
                external_post_id="post-1",
                source_url="https://example.com/post-1",
                recruiter_email="sam@acme.com",
                recruiter_phone="+12145550000",
                recruiter_name="Sam",
                company="Acme",
                role="Java Lead",
                location="Austin, TX",
                work_mode="Hybrid",
                visa_hints="",
                duration="6 months",
                rate="$70/hr",
                skills_text="Java, Spring Boot, Kafka",
                raw_body="Required skills: Java and Spring Boot. Preferred: Kafka.",
                raw_html="",
                dedupe_hash="hash-1",
            )
            db.add(external)
            db.commit()
            db.refresh(external)
            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="msg-2",
                source_type="nvoids",
                source_url=external.source_url,
                external_opportunity_id=external.id,
                email_subject="Java Lead role",
                email_sender="sam@acme.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Java Lead",
                client="Acme",
                location="Austin, TX",
                work_mode="Hybrid",
                visa_restrictions="",
                extracted_skills="java, spring boot, kafka",
                evidence="External feed: nvoids",
                status="New",
                notes="",
            )
            db.add(opportunity)
            db.commit()
            db.refresh(opportunity)
            return opportunity.id

    def test_generate_cold_call_script_updates_and_returns_fields(self) -> None:
        opportunity_id = self._seed_opportunity()
        with patch(
            "app.main.generate_cold_call_script",
            return_value="Hi, this is Chaithanya calling about your Java Lead role. My background aligns with your needs. Could we discuss next steps?",
        ):
            response = self.client.post(f"/recruiter-opportunities/{opportunity_id}/generate-cold-call-script")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("cold_call_script", payload)
        self.assertTrue(payload["cold_call_script"])
        self.assertIsNotNone(payload["cold_call_script_updated_at"])

    def test_generate_cold_call_script_uses_gmail_requirement_text(self) -> None:
        opportunity_id = self._seed_gmail_backed_opportunity()
        with patch("app.main.find_allowed_cold_call_skills", return_value=[]) as overlap:
            with patch("app.main.generate_cold_call_script", return_value="Safe script"):
                response = self.client.post(f"/recruiter-opportunities/{opportunity_id}/generate-cold-call-script")
        self.assertEqual(response.status_code, 200, response.text)
        requirement_text = overlap.call_args.kwargs["requirement_text"]
        self.assertIn("Required Skills: Java, Spring Boot, Kubernetes", requirement_text)

    def test_generate_cold_call_script_uses_nvoids_requirement_text(self) -> None:
        opportunity_id = self._seed_nvoids_backed_opportunity()
        with patch("app.main.find_allowed_cold_call_skills", return_value=[]) as overlap:
            with patch("app.main.generate_cold_call_script", return_value="Safe script"):
                response = self.client.post(f"/recruiter-opportunities/{opportunity_id}/generate-cold-call-script")
        self.assertEqual(response.status_code, 200, response.text)
        requirement_text = overlap.call_args.kwargs["requirement_text"]
        self.assertIn("Required skills: Java and Spring Boot. Preferred: Kafka.", requirement_text)

    def test_generate_cold_call_script_returns_404_for_missing_id(self) -> None:
        response = self.client.post("/recruiter-opportunities/999999/generate-cold-call-script")
        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":
    unittest.main()

