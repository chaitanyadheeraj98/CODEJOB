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
from app.models import NumberReviewQueue, PremiumNumberLead, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, UserSettings


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

    def test_recruiter_opportunity_includes_recruiter_phone_fields(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Vaishnavi <vaishnavi@horizonsoftech.net>",
                subject="FW: Looking for Full Stack Developer",
                body="Call +1 214 393 8746",
                role="Full Stack Developer",
                location="onsite",
                salary_text="",
                skills_text="python,sql,react,java",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-opportunity-phone",
                external_thread_id="t-opportunity-phone",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=email.id,
                    normalized_phone_number="+12143938746",
                    display_phone_number="+1 214 393 8746",
                    owner_name="Dharma Veer",
                    company="Teamware Solutions",
                    designation="Talent Acquisition Specialist",
                    confidence="high",
                    purpose="Recruiter contact number",
                    evidence_snippet="Extracted by AI from email context",
                    email_subject=email.subject,
                    email_sender="dharma.veer@intellisoft.com",
                    gmail_open_url=email.gmail_message_url or "",
                    state="pending",
                )
            )
            db.commit()
            card = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == main.settings.owner_id).first()
            assert card is not None
            review_id = card.id

        mark_res = self.client.post(f"/number-review/{review_id}/mark-recruiter")
        self.assertEqual(mark_res.status_code, 200, mark_res.text)

        list_res = self.client.get("/recruiter-opportunities")
        self.assertEqual(list_res.status_code, 200, list_res.text)
        payload = list_res.json()
        self.assertGreaterEqual(len(payload), 1)
        self.assertIn("recruiter_phone_display", payload[0])
        self.assertEqual(payload[0]["recruiter_phone_display"], "+1 214 393 8746")
        self.assertEqual(payload[0]["recruiter_name"], "Dharma Veer")
        self.assertEqual(payload[0]["recruiter_email"], "dharma.veer@intellisoft.com")

    def test_delete_recruiter_opportunity_deletes_orphan_recruiter_number(self) -> None:
        with Session(self.engine) as db:
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550000",
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

            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="msg-delete-single",
                email_subject="Delete me",
                email_sender="sam@acme.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Engineer",
                client="Acme",
                location="Austin, TX",
                work_mode="Remote",
                visa_restrictions="",
                extracted_skills="java",
                evidence="test",
                status="New",
                notes="",
            )
            db.add(opportunity)
            db.commit()
            db.refresh(opportunity)
            opportunity_id = opportunity.id
            recruiter_id = recruiter.id

        response = self.client.delete(f"/recruiter-opportunities/{opportunity_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], opportunity_id)
        self.assertTrue(payload["deleted"])
        self.assertTrue(payload["recruiter_number_deleted"])

        with Session(self.engine) as db:
            remaining_opp = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == opportunity_id).first()
            self.assertIsNone(remaining_opp)
            remaining_recruiter = db.query(RecruiterNumber).filter(RecruiterNumber.id == recruiter_id).first()
            self.assertIsNone(remaining_recruiter)

    def test_delete_recruiter_opportunity_keeps_recruiter_when_other_opportunities_exist(self) -> None:
        with Session(self.engine) as db:
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550001",
                display_phone_number="+1 214 555 0001",
                recruiter_name="Alex",
                company="BrightPath",
                designation="Recruiter",
                recruiter_email="alex@brightpath.com",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.commit()
            db.refresh(recruiter)

            first = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="msg-delete-keep-1",
                email_subject="Delete first",
                email_sender="alex@brightpath.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Engineer",
                client="BrightPath",
                location="Dallas, TX",
                work_mode="Hybrid",
                visa_restrictions="",
                extracted_skills="java,spring",
                evidence="test",
                status="New",
                notes="",
            )
            second = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=None,
                gmail_message_id="msg-delete-keep-2",
                email_subject="Keep second",
                email_sender="alex@brightpath.com",
                gmail_open_url="",
                received_at=datetime.now(UTC),
                job_title="Senior Engineer",
                client="BrightPath",
                location="Dallas, TX",
                work_mode="Hybrid",
                visa_restrictions="",
                extracted_skills="aws",
                evidence="test",
                status="New",
                notes="",
            )
            db.add(first)
            db.add(second)
            db.commit()
            db.refresh(first)
            db.refresh(second)
            first_id = first.id
            second_id = second.id
            recruiter_id = recruiter.id

        response = self.client.delete(f"/recruiter-opportunities/{first_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertTrue(payload["deleted"])
        self.assertFalse(payload["recruiter_number_deleted"])

        with Session(self.engine) as db:
            deleted = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == first_id).first()
            self.assertIsNone(deleted)
            survivor = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == second_id).first()
            self.assertIsNotNone(survivor)
            remaining_recruiter = db.query(RecruiterNumber).filter(RecruiterNumber.id == recruiter_id).first()
            self.assertIsNotNone(remaining_recruiter)

    def test_delete_recruiter_opportunity_returns_404_for_missing_id(self) -> None:
        response = self.client.delete("/recruiter-opportunities/999999")
        self.assertEqual(response.status_code, 404, response.text)

    def test_reextract_is_idempotent_for_leads_and_opportunities(self) -> None:
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
                    employer_domains="horizonsoftech.net",
                    free_text_guidance="",
                    remote_preference="any",
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Prashanth <kprashanth@horizonsoftech.net>",
                subject="Java role",
                body="Call Dharma Veer at +1 (972) 756-1212 Ext 128",
                role="Java Developer",
                location="onsite",
                salary_text="",
                skills_text="java,spring",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-reextract-idempotent",
                external_thread_id="t-reextract-idempotent",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="19727561212",
                    display_phone_number="(972) 756-1212 ext 128",
                    recruiter_name="Dharma Veer",
                    company="Intellisoft",
                    designation="US IT Recruiter",
                    recruiter_email="dharma.veer@intellisoft.com",
                    first_detected_email_id=email.id,
                )
            )
            db.commit()
            email_id = email.id

        first = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["stored_count"], 1)

        second = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["stored_count"], 1)

        with Session(self.engine) as db:
            leads = (
                db.query(PremiumNumberLead)
                .filter(
                    PremiumNumberLead.owner_id == main.settings.owner_id,
                    PremiumNumberLead.recruiter_email_id == email_id,
                    PremiumNumberLead.phone_number_normalized == "19727561212",
                )
                .count()
            )
            opportunities = (
                db.query(RecruiterOpportunity)
                .filter(
                    RecruiterOpportunity.owner_id == main.settings.owner_id,
                    RecruiterOpportunity.gmail_message_id == "m-reextract-idempotent",
                )
                .count()
            )
            self.assertEqual(leads, 1)
            self.assertEqual(opportunities, 1)

    def test_reextract_enriches_existing_unknown_recruiter_name(self) -> None:
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
                    employer_domains="horizonsoftech.net",
                    free_text_guidance="",
                    remote_preference="any",
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Samshritha <samshritha@horizonsoftech.net>",
                subject="Senior Talend Developer",
                body="please share the suitable resume to Rabbanis@kgatetech.com - +1 832-271-3861",
                role="Senior Talend Developer",
                location="onsite",
                salary_text="",
                skills_text="sql,java,aws",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-rabbanis-1",
                external_thread_id="t-rabbanis-1",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)

            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="18322713861",
                    display_phone_number="(832) 271-3861",
                    recruiter_name="Unknown",
                    company="Unknown",
                    designation="Unknown",
                    recruiter_email="samshritha@horizonsoftech.net",
                    first_detected_email_id=email.id,
                )
            )
            db.commit()
            email_id = email.id

        response = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreaterEqual(response.json().get("stored_count", 0), 1)

        with Session(self.engine) as db:
            recruiter = (
                db.query(RecruiterNumber)
                .filter(
                    RecruiterNumber.owner_id == main.settings.owner_id,
                    RecruiterNumber.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Rabbanis")

    def test_reextract_overrides_signature_name_with_target_contact_name(self) -> None:
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
                    employer_domains="horizonsoftech.net",
                    free_text_guidance="",
                    remote_preference="any",
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Samshritha Gangula <samshritha@horizonsoftech.net>",
                subject="Senior Talend Developer",
                body=(
                    "please share the suitable resume to \n"
                    "<mailto:Rabbanis@kgatetech.com> Rabbanis@kgatetech.com - +1 832-271-3861\n"
                    "Thanks & Regards\n"
                    "Samshritha Gangula\n"
                    "Bench Sales Recruiter"
                ),
                role="Senior Talend Developer",
                location="onsite",
                salary_text="",
                skills_text="sql,java,aws",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-rabbanis-override-1",
                external_thread_id="t-rabbanis-override-1",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)

            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="18322713861",
                    display_phone_number="(832) 271-3861",
                    recruiter_name="Samshritha Gangula",
                    company="Unknown",
                    designation="Unknown",
                    recruiter_email="samshritha@horizonsoftech.net",
                    first_detected_email_id=email.id,
                )
            )
            db.commit()
            email_id = email.id

        response = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            recruiter = (
                db.query(RecruiterNumber)
                .filter(
                    RecruiterNumber.owner_id == main.settings.owner_id,
                    RecruiterNumber.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Rabbanis")

    def test_reextract_overrides_name_without_to_when_contact_snippet_is_strong(self) -> None:
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
                    employer_domains="horizonsoftech.net",
                    free_text_guidance="",
                    remote_preference="any",
                )
            )
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Samshritha Gangula <samshritha@horizonsoftech.net>",
                subject="Talend Developer",
                body=(
                    "please share suitable profile at Rabbanis@kgatetech.com - +1 832-271-3861\n"
                    "Regards,\n"
                    "Samshritha Gangula"
                ),
                role="Talend Developer",
                location="onsite",
                salary_text="",
                skills_text="sql,java,aws",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-rabbanis-no-to-1",
                external_thread_id="t-rabbanis-no-to-1",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.commit()
            db.refresh(email)

            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="18322713861",
                    display_phone_number="(832) 271-3861",
                    recruiter_name="Samshritha Gangula",
                    company="Unknown",
                    designation="Unknown",
                    recruiter_email="samshritha@horizonsoftech.net",
                    first_detected_email_id=email.id,
                )
            )
            db.commit()
            email_id = email.id

        response = self.client.post(f"/premium-numbers/reextract/{email_id}")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            recruiter = (
                db.query(RecruiterNumber)
                .filter(
                    RecruiterNumber.owner_id == main.settings.owner_id,
                    RecruiterNumber.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Rabbanis")


if __name__ == "__main__":
    unittest.main()
