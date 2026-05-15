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
from app.models import PremiumNumberLead, RecruiterEmail, UserSettings


class PremiumNumbersApiTests(unittest.TestCase):
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

    def test_list_premium_numbers_filters_confidence(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="recruiter@example.com",
                subject="Role",
                body="Body",
                role="Developer",
                location="Remote",
                salary_text="",
                skills_text="Python",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-premium-1",
                external_thread_id="t-premium-1",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=email.id,
                    phone_number_normalized="+12145551212",
                    phone_number_display="+1 (214) 555-1212",
                    owner_name="Uma",
                    company="BrightPath",
                    designation="Recruiter",
                    purpose="Recruiter direct number",
                    confidence="high",
                    contact_type="recruiter_direct",
                    recruiter_relevance_score=90,
                    is_recruiter_relevant=True,
                    relevance_reason="external_domain,purpose_positive",
                    source_fragment="call me",
                    source_email_sender=email.sender,
                    source_email_subject=email.subject,
                    source_email_message_id=email.external_message_id,
                )
            )
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=email.id,
                    phone_number_normalized="+12482476165",
                    phone_number_display="+1 248 247 6165",
                    owner_name="Internal Recruiter",
                    company="Horizon Softech Inc",
                    designation="Bench Sales Recruiter",
                    purpose="Office contact number",
                    confidence="high",
                    contact_type="employer_internal",
                    recruiter_relevance_score=15,
                    is_recruiter_relevant=False,
                    relevance_reason="employer_domain,purpose_negative",
                    source_fragment="office",
                    source_email_sender="internal@horizonsoftech.net",
                    source_email_subject=email.subject,
                    source_email_message_id=email.external_message_id,
                )
            )
            db.commit()

        response = self.client.get("/premium-numbers", params={"confidence": "high"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["owner_name"], "Uma")
        self.assertTrue(payload["items"][0]["is_recruiter_relevant"])

        show_all = self.client.get("/premium-numbers", params={"confidence": "high", "recruiter_only": "false"})
        self.assertEqual(show_all.status_code, 200, show_all.text)
        show_all_payload = show_all.json()
        self.assertEqual(len(show_all_payload["items"]), 2)

    def test_reextract_skips_non_employer_sender_domain(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    enabled=True,
                    gmail_query="is:unread",
                    default_gmail_query="is:unread",
                    default_date_mode="today",
                    accepted_locations="",
                    role_keywords="",
                    must_have_skills="",
                    employer_domains="horizonsoftech.net,rpatechnologyinc.com",
                    free_text_guidance="",
                    remote_preference="any",
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="LinkedIn <jobs-listings@linkedin.com>",
                subject="Twine is hiring",
                body="Call 4292809173",
                role="Developer",
                location="Remote",
                salary_text="",
                skills_text="Python",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-premium-non-employer",
                external_thread_id="t-premium-non-employer",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            email_id = email.id

        response = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stored_count"], 0)

        with Session(self.engine) as db:
            count = (
                db.query(PremiumNumberLead)
                .filter(PremiumNumberLead.owner_id == main.settings.owner_id, PremiumNumberLead.recruiter_email_id == email_id)
                .count()
            )
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
