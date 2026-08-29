import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    NumberReviewQueue,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    PremiumNumberContact,
    PremiumNumberExtractionAudit,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
    UserSettings,
)
from app.premium_numbers.extraction import ExtractedContactGroup
from app.services import opportunity_lineage_service


def RecruiterNumber(**values):
    return PremiumNumberContact(is_recruiter=True, **values)


def EmployerNumber(**values):
    return PremiumNumberContact(is_employer=True, **values)


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

    def test_reextract_no_longer_skips_non_employer_sender_domain(self) -> None:
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
        self.assertEqual(response.json()["stored_count"], 1)

        with Session(self.engine) as db:
            count = (
                db.query(PremiumNumberLead)
                .filter(PremiumNumberLead.owner_id == main.settings.owner_id, PremiumNumberLead.recruiter_email_id == email_id)
                .count()
            )
            self.assertEqual(count, 1)

    def test_mark_number_as_recruiter_accepts_phone_less_review_card(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Mani <mani@itbtalent.com>",
                subject="Java Developer opening",
                body="Reach me at mani@itbtalent.com, no direct line available.",
                role="Java Developer",
                location="remote",
                salary_text="",
                skills_text="java",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-no-phone",
                external_thread_id="t-no-phone",
                gmail_received_at=now,
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=email.id,
                    normalized_phone_number="",
                    display_phone_number="",
                    owner_name="Mani",
                    company="ITB Talent",
                    designation="Recruiter",
                    confidence="medium",
                    purpose="Recruiter contact",
                    evidence_snippet="Reach me at mani@itbtalent.com",
                    email_subject=email.subject,
                    email_sender="mani@itbtalent.com",
                    contact_email="mani@itbtalent.com",
                    gmail_open_url=email.gmail_message_url or "",
                    state="pending",
                )
            )
            db.commit()
            review_id = (
                db.query(NumberReviewQueue.id)
                .filter(NumberReviewQueue.owner_id == main.settings.owner_id)
                .scalar()
            )

        mark_res = self.client.post(f"/number-review/{review_id}/mark-recruiter")
        self.assertEqual(mark_res.status_code, 200, mark_res.text)

        with Session(self.engine) as db:
            contact = db.query(PremiumNumberContact).filter(
                PremiumNumberContact.recruiter_email == "mani@itbtalent.com"
            ).one()
            self.assertIsNone(contact.normalized_phone_number)
            self.assertTrue(contact.is_recruiter)

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
            lineage = opportunity_lineage_service.create_lineage(
                db,
                owner_id=main.settings.owner_id,
                origin_type="gmail",
                source_type="gmail",
                external_id=str(email.id),
                source_url=email.gmail_message_url or "",
                process_name="phone_intelligence_workflow",
            )
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    lineage_id=lineage.id,
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
        self.assertGreaterEqual(len(payload["items"]), 1)
        self.assertIn("recruiter_phone_display", payload["items"][0])
        self.assertEqual(payload["items"][0]["recruiter_phone_display"], "(214) 393-8746")
        self.assertEqual(payload["items"][0]["recruiter_name"], "Dharma Veer")
        self.assertEqual(payload["items"][0]["recruiter_email"], "dharma.veer@intellisoft.com")
        with Session(self.engine) as db:
            card = db.get(NumberReviewQueue, review_id)
            opportunity = db.query(RecruiterOpportunity).one()
            lineage = db.query(OpportunityLineage).one()
            self.assertEqual(card.lineage_id, lineage.id)
            self.assertEqual(lineage.recruiter_opportunity_id, opportunity.id)
            self.assertEqual(
                db.query(OpportunityLifecycleEvent)
                .filter_by(lineage_id=lineage.id, event_type="promoted")
                .count(),
                1,
            )

    def test_mark_number_as_recruiter_derives_name_and_company_from_email_when_unknown(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="bindu.k@saranshinc.com",
                subject="Recruiter contact",
                body="Call +1 609 757 4143",
                role="Engineer",
                location="remote",
                salary_text="",
                skills_text="",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-unknown-name-company",
                external_thread_id="t-unknown-name-company",
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
                    normalized_phone_number="+16097574143",
                    display_phone_number="+1 609 757 4143",
                    owner_name="Unknown",
                    company="Unknown",
                    designation="Recruiter",
                    confidence="medium",
                    purpose="Recruiter direct number",
                    evidence_snippet="Extracted by AI from email context",
                    email_subject=email.subject,
                    email_sender="bindu.k@saranshinc.com",
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

        with Session(self.engine) as db:
            recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).first()
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Bindu K")
            self.assertEqual(recruiter.company, "Saranshinc")

    def test_mark_number_as_employer_standardizes_display_phone(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Recruiter <recruiter@example.com>",
                subject="Role",
                body="Call +1 (980) 9070802",
                role="Engineer",
                location="Remote",
                salary_text="",
                skills_text="java",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="m-standardize-employer",
                external_thread_id="t-standardize-employer",
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
                    normalized_phone_number="19809070802",
                    display_phone_number="+1 (980) 9070802",
                    owner_name="Employer Owner",
                    company="Perficient",
                    designation="Recruiter",
                    confidence="high",
                    purpose="Contact number",
                    evidence_snippet="snippet",
                    email_subject=email.subject,
                    email_sender="recruiter@example.com",
                    gmail_open_url=email.gmail_message_url or "",
                    state="pending",
                )
            )
            db.commit()
            card = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == main.settings.owner_id).first()
            assert card is not None
            review_id = card.id

        response = self.client.post(f"/number-review/{review_id}/mark-employer")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            employer = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).first()
            self.assertIsNotNone(employer)
            assert employer is not None
            self.assertEqual(employer.display_phone_number, "(980) 907-0802")

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
            db.flush()
            lineage = opportunity_lineage_service.create_lineage(
                db,
                owner_id=main.settings.owner_id,
                origin_type="gmail",
                source_type="gmail",
                external_id="",
                source_url="",
                process_name="test_fixture",
                recruiter_opportunity_id=opportunity.id,
            )
            db.commit()
            db.refresh(opportunity)
            opportunity_id = opportunity.id
            recruiter_id = recruiter.id
            lineage_id = lineage.id

        response = self.client.delete(f"/recruiter-opportunities/{opportunity_id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], opportunity_id)
        self.assertTrue(payload["deleted"])
        self.assertTrue(payload["recruiter_number_deleted"])

        with Session(self.engine) as db:
            remaining_opp = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.id == opportunity_id).first()
            self.assertIsNone(remaining_opp)
            remaining_recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == recruiter_id).first()
            self.assertIsNone(remaining_recruiter)
            lineage = db.get(OpportunityLineage, lineage_id)
            self.assertIsNone(lineage.recruiter_opportunity_id)
            self.assertEqual(lineage.current_status, "closed")
            self.assertIsNotNone(lineage.closed_at)
            self.assertEqual(
                db.query(OpportunityLifecycleEvent)
                .filter_by(lineage_id=lineage_id, event_type="deleted")
                .count(),
                1,
            )

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
            remaining_recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == recruiter_id).first()
            self.assertIsNotNone(remaining_recruiter)

    def test_swap_recruiter_number_to_employer_standardizes_display_phone(self) -> None:
        with Session(self.engine) as db:
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12012772419",
                display_phone_number="+1 (201) 277-2419",
                recruiter_name="Unknown",
                company="Unknown",
                designation="Recruiter",
                recruiter_email="riyas@example.com",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.commit()
            db.refresh(recruiter)
            recruiter_id = recruiter.id

        response = self.client.post(f"/recruiter-numbers/{recruiter_id}/swap-to-employer")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            employer = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).first()
            self.assertIsNotNone(employer)
            assert employer is not None
            self.assertEqual(employer.display_phone_number, "(201) 277-2419")

    def test_swap_employer_number_to_recruiter_standardizes_display_phone(self) -> None:
        with Session(self.engine) as db:
            employer = EmployerNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="19809070802",
                display_phone_number="+1 (980) 9070802",
                owner_name="Owner",
                company="Corp",
                source_email_id=None,
            )
            db.add(employer)
            db.commit()
            db.refresh(employer)
            employer_id = employer.id

        response = self.client.post(f"/employer-numbers/{employer_id}/swap-to-recruiter")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == main.settings.owner_id).first()
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.display_phone_number, "(980) 907-0802")

    def test_employer_numbers_hides_unparseable_junk_rows(self) -> None:
        with Session(self.engine) as db:
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12487222694",
                    display_phone_number="(248) 722-2694",
                    owner_name="Valid Owner",
                    company="Valid Corp",
                    source_email_id=None,
                )
            )
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="63686972753038383338393540676",
                    display_phone_number="636869727530 38383338393540676",
                    owner_name="Junk Owner",
                    company="Junk Corp",
                    source_email_id=None,
                )
            )
            db.commit()

        response = self.client.get("/employer-numbers")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["company"], "Valid Corp")
        self.assertEqual(payload["items"][0]["display_phone_number"], "(248) 722-2694")

    def test_number_review_supports_pagination_and_search(self) -> None:
        with Session(self.engine) as db:
            db.add_all(
                [
                    NumberReviewQueue(
                        owner_id=main.settings.owner_id,
                        source_email_id=1,
                        normalized_phone_number="12405550111",
                        display_phone_number="(240) 555-0111",
                        owner_name="Nancy Recruiter",
                        company="Oss",
                        designation="Recruiter",
                        confidence="high",
                        purpose="Recruiter contact number",
                        evidence_snippet="snippet",
                        email_subject="Java role",
                        email_sender="nancy@example.com",
                        gmail_open_url="",
                        state="pending",
                    ),
                    NumberReviewQueue(
                        owner_id=main.settings.owner_id,
                        source_email_id=2,
                        normalized_phone_number="12015550112",
                        display_phone_number="(201) 555-0112",
                        owner_name="Riya",
                        company="Smart",
                        designation="Recruiter",
                        confidence="high",
                        purpose="Recruiter contact number",
                        evidence_snippet="snippet",
                        email_subject="Python role",
                        email_sender="riya@example.com",
                        gmail_open_url="",
                        state="pending",
                    ),
                ]
            )
            db.commit()

        response = self.client.get("/number-review", params={"limit": 1, "q": "nancy"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["owner_name"], "Nancy Recruiter")
        self.assertFalse(payload["has_next"])
        self.assertIsNone(payload["next_cursor"])

    def test_recruiter_numbers_support_pagination_and_search(self) -> None:
        with Session(self.engine) as db:
            db.add_all(
                [
                    RecruiterNumber(
                        owner_id=main.settings.owner_id,
                        normalized_phone_number="12406571540",
                        display_phone_number="(240) 657-1540",
                        recruiter_name="Nancy",
                        company="OSS",
                        designation="Recruiter",
                        recruiter_email="nancy@ossinc.us.com",
                        first_detected_email_id=None,
                    ),
                    RecruiterNumber(
                        owner_id=main.settings.owner_id,
                        normalized_phone_number="12012772419",
                        display_phone_number="(201) 277-2419",
                        recruiter_name="Riyas",
                        company="Smart IT",
                        designation="Recruiter",
                        recruiter_email="riyas@example.com",
                        first_detected_email_id=None,
                    ),
                ]
            )
            db.commit()

        response = self.client.get("/recruiter-numbers", params={"limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertTrue(payload["has_next"])
        self.assertEqual(payload["next_cursor"], 1)

        search = self.client.get("/recruiter-numbers", params={"q": "ossinc"})
        self.assertEqual(search.status_code, 200, search.text)
        search_payload = search.json()
        self.assertEqual(len(search_payload["items"]), 1)
        self.assertEqual(search_payload["items"][0]["recruiter_email"], "nancy@ossinc.us.com")

    def test_employer_numbers_support_pagination_and_search(self) -> None:
        with Session(self.engine) as db:
            db.add_all(
                [
                    EmployerNumber(
                        owner_id=main.settings.owner_id,
                        normalized_phone_number="12487222694",
                        display_phone_number="(248) 722-2694",
                        owner_name="Mohan",
                        company="Horizon Softech Inc",
                        source_email_id=None,
                    ),
                    EmployerNumber(
                        owner_id=main.settings.owner_id,
                        normalized_phone_number="12404649780",
                        display_phone_number="(240) 464-9780",
                        owner_name="Alekya",
                        company="Rpa",
                        source_email_id=None,
                    ),
                ]
            )
            db.commit()

        response = self.client.get("/employer-numbers", params={"limit": 1})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertTrue(payload["has_next"])
        self.assertEqual(payload["next_cursor"], 1)

        search = self.client.get("/employer-numbers", params={"q": "mohan"})
        self.assertEqual(search.status_code, 200, search.text)
        search_payload = search.json()
        self.assertEqual(len(search_payload["items"]), 1)
        self.assertEqual(search_payload["items"][0]["owner_name"], "Mohan")

    def test_recruiter_opportunities_support_pagination_search_and_filters(self) -> None:
        with Session(self.engine) as db:
            recruiter = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550111",
                display_phone_number="(214) 555-0111",
                recruiter_name="Visible Recruiter",
                company="Visible Co",
                designation="Recruiter",
                recruiter_email="visible@example.com",
                first_detected_email_id=None,
            )
            db.add(recruiter)
            db.flush()
            db.add_all(
                [
                    RecruiterOpportunity(
                        owner_id=main.settings.owner_id,
                        recruiter_number_id=recruiter.id,
                        source_email_id=None,
                        gmail_message_id="opp-1",
                        source_type="gmail",
                        source_url=None,
                        external_opportunity_id=None,
                        email_subject="Java Engineer",
                        email_sender="visible@example.com",
                        gmail_open_url="",
                        received_at=datetime.now(UTC),
                        job_title="Java Engineer",
                        client="Visible Co",
                        location="Texas",
                        work_mode="Remote",
                        visa_restrictions="",
                        extracted_skills="Java, Spring",
                        evidence="gmail",
                        status="New",
                        notes="",
                    ),
                    RecruiterOpportunity(
                        owner_id=main.settings.owner_id,
                        recruiter_number_id=recruiter.id,
                        source_email_id=None,
                        gmail_message_id="opp-2",
                        source_type="nvoids",
                        source_url="https://nvoids.com/job_details.jsp?id=2",
                        external_opportunity_id=2,
                        email_subject="Python Engineer",
                        email_sender="visible@example.com",
                        gmail_open_url="",
                        received_at=datetime.now(UTC),
                        job_title="Python Engineer",
                        client="Visible Co",
                        location="Texas",
                        work_mode="Remote",
                        visa_restrictions="",
                        extracted_skills="Python",
                        evidence="nvoids",
                        status="Called",
                        notes="",
                    ),
                ]
            )
            db.commit()

        response = self.client.get("/recruiter-opportunities", params={"limit": 1, "status": "New", "q": "214"})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["job_title"], "Java Engineer")
        self.assertFalse(payload["has_next"])
        self.assertIsNone(payload["next_cursor"])

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

        extracted = ExtractedContactGroup(
            phone_number_display="(972) 756-1212 ext 128",
            phone_number_normalized="19727561212",
            owner_name="Dharma Veer",
            contact_email="dharma.veer@intellisoft.com",
            company="Intellisoft",
            designation="US IT Recruiter",
            purpose="Recruiter contact",
            confidence="high",
            contact_type="recruiter_direct",
            recruiter_relevance_score=90,
            is_recruiter_relevant=True,
            relevance_reason="recruiter_role_and_domain",
            source_fragment="Call Dharma Veer at +1 (972) 756-1212 Ext 128",
            role="recruiter",
            extraction_source="ai",
        )
        with patch(
            "app.services.phone_intelligence_workflow_service.extract_phone_leads",
            return_value=[extracted],
        ):
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
                    PremiumNumberLead.extraction_source == "ai",
                )
                .count()
            )
            versions = (
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
            self.assertEqual(versions, 2)  # one immutable legacy snapshot plus the AI version
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
                db.query(PremiumNumberContact)
                .filter(
                    PremiumNumberContact.owner_id == main.settings.owner_id,
                    PremiumNumberContact.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Unknown")

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
                db.query(PremiumNumberContact)
                .filter(
                    PremiumNumberContact.owner_id == main.settings.owner_id,
                    PremiumNumberContact.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Samshritha Gangula")

    def test_review_submit_appends_manual_version_and_normalizes_linkedin(self) -> None:
        with Session(self.engine) as db:
            source = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                recruiter_email_id=None,
                phone_number_normalized="12145551212",
                phone_number_display="(214) 555-1212",
                role="unknown",
                extraction_source="regex_fallback",
                owner_name="Unknown",
                company="Unknown",
                designation="Unknown",
            )
            db.add(source)
            db.flush()
            card = NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=None,
                source_lead_id=source.id,
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                owner_name="Unknown",
                company="Unknown",
                designation="Unknown",
                email_subject="Role",
                email_sender="sender@agency.example",
                state="pending",
            )
            db.add(card)
            db.commit()
            review_id = card.id

        response = self.client.post(
            f"/number-review/{review_id}/mark-recruiter",
            json={
                "owner_name": "Priya Sharma",
                "company": "Agency Co",
                "contact_email": "priya@agency.example",
                "designation": "Senior Recruiter",
                "linkedin_url": "linkedin.com/in/priya-sharma",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            contact = db.query(PremiumNumberContact).one()
            self.assertEqual(contact.recruiter_name, "Priya Sharma")
            self.assertEqual(contact.linkedin_url, "https://linkedin.com/in/priya-sharma")
            active = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertEqual(active.extraction_source, "manual_review")
            self.assertEqual(active.contact_email, "priya@agency.example")
            self.assertEqual(db.query(PremiumNumberLead).count(), 2)

    def test_number_review_submit_no_edits_links_existing_lead(self) -> None:
        with Session(self.engine) as db:
            source = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                recruiter_email_id=None,
                phone_number_normalized="12145551313",
                phone_number_display="(214) 555-1313",
                role="unknown",
                extraction_source="ai",
                owner_name="Priya Sharma",
                company="Agency Co",
                designation="Senior Recruiter",
                contact_email="priya@agency.example",
            )
            db.add(source)
            db.flush()
            card = NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=None,
                source_lead_id=source.id,
                normalized_phone_number="12145551313",
                display_phone_number="(214) 555-1313",
                owner_name="Priya Sharma",
                company="Agency Co",
                designation="Senior Recruiter",
                contact_email="priya@agency.example",
                email_subject="Role",
                email_sender="priya@agency.example",
                state="pending",
            )
            db.add(card)
            db.commit()
            review_id = card.id
            source_lead_id = source.id
            leads_before = db.query(PremiumNumberLead).count()

        # No body at all -- exercises the same "nothing edited" path as an
        # explicit NumberReviewSubmitRequest whose fields are all None/match
        # the card's stored values exactly (see _review_fields_edited).
        response = self.client.post(f"/number-review/{review_id}/mark-recruiter")
        self.assertEqual(response.status_code, 200, response.text)

        with Session(self.engine) as db:
            # No redundant manual_review duplicate was created -- row count unchanged.
            self.assertEqual(db.query(PremiumNumberLead).count(), leads_before)

            contact = db.query(PremiumNumberContact).one()
            self.assertEqual(contact.active_recruiter_lead_id, source_lead_id)

            active = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertEqual(active.id, source_lead_id)
            self.assertEqual(active.extraction_source, "ai")

            card = db.get(NumberReviewQueue, review_id)
            self.assertEqual(card.state, "classified_recruiter")

    def test_bulk_review_actions_report_each_id_without_route_collision(self) -> None:
        with Session(self.engine) as db:
            first = NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=None,
                normalized_phone_number="12145550001",
                display_phone_number="(214) 555-0001",
                owner_name="One",
                company="Agency",
                designation="Recruiter",
                email_subject="Role one",
                email_sender="one@agency.example",
                state="pending",
            )
            second = NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=None,
                normalized_phone_number="12145550002",
                display_phone_number="(214) 555-0002",
                owner_name="Two",
                company="Client",
                designation="Manager",
                email_subject="Role two",
                email_sender="two@client.example",
                state="pending",
            )
            other_owner = NumberReviewQueue(
                owner_id="other-owner",
                source_email_id=None,
                normalized_phone_number="12145550003",
                display_phone_number="(214) 555-0003",
                owner_name="Other",
                company="Other",
                designation="Recruiter",
                state="pending",
            )
            db.add_all([first, second, other_owner])
            db.commit()
            first_id, second_id, other_id = first.id, second.id, other_owner.id

        marked = self.client.post(
            "/number-review/bulk-mark-recruiter",
            json={"review_ids": [first_id, other_id, 999999]},
        )
        self.assertEqual(marked.status_code, 200, marked.text)
        self.assertEqual(
            marked.json()["results"],
            [
                {"review_id": first_id, "status": "classified_recruiter"},
                {"review_id": other_id, "status": "not_found"},
                {"review_id": 999999, "status": "not_found"},
            ],
        )

        deleted = self.client.post(
            "/number-review/bulk-delete",
            json={"review_ids": [second_id, first_id]},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            [item["status"] for item in deleted.json()["results"]],
            ["dismissed", "classified_recruiter"],
        )

    def test_select_version_updates_active_lead_id(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550100",
                display_phone_number="(214) 555-0100",
                is_recruiter=True,
                recruiter_name="Old Name",
            )
            db.add(contact)
            db.flush()
            old = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                recruiter_email_id=None,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Old Name",
                company="Old Co",
                designation="Recruiter",
                contact_email="old@example.com",
            )
            new = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                recruiter_email_id=None,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="New Name",
                company="New Co",
                designation="Director",
                contact_email="new@example.com",
            )
            db.add_all([old, new])
            db.flush()
            contact.active_recruiter_lead_id = old.id
            db.commit()
            contact_id, new_id = contact.id, new.id

        versions = self.client.get(f"/recruiter-numbers/{contact_id}/versions")
        self.assertEqual(versions.status_code, 200, versions.text)
        self.assertEqual(len(versions.json()), 2)
        selected = self.client.post(f"/recruiter-numbers/{contact_id}/select-version/{new_id}")
        self.assertEqual(selected.status_code, 200, selected.text)
        with Session(self.engine) as db:
            contact = db.get(PremiumNumberContact, contact_id)
            self.assertEqual(contact.active_recruiter_lead_id, new_id)
            self.assertEqual(contact.recruiter_name, "New Name")
            self.assertEqual(contact.recruiter_email, "new@example.com")

    def test_delete_inactive_version_leaves_active_version_untouched(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550101",
                display_phone_number="(214) 555-0101",
                is_recruiter=True,
                recruiter_name="Active Name",
            )
            db.add(contact)
            db.flush()
            active = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Active Name",
                company="Active Co",
            )
            inactive = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Legacy Name",
                company="Legacy Co",
            )
            db.add_all([active, inactive])
            db.flush()
            contact.active_recruiter_lead_id = active.id
            db.commit()
            contact_id, active_id, inactive_id = contact.id, active.id, inactive.id

        deleted = self.client.delete(f"/recruiter-numbers/{contact_id}/versions/{inactive_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        with Session(self.engine) as db:
            contact = db.get(PremiumNumberContact, contact_id)
            self.assertEqual(contact.active_recruiter_lead_id, active_id)
            self.assertEqual(contact.recruiter_name, "Active Name")
            self.assertIsNone(db.get(PremiumNumberLead, inactive_id))
        versions = self.client.get(f"/recruiter-numbers/{contact_id}/versions")
        self.assertEqual(len(versions.json()), 1)

    def test_delete_active_version_switches_to_remaining_version(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550102",
                display_phone_number="(214) 555-0102",
                is_recruiter=True,
                recruiter_name="Legacy Name",
            )
            db.add(contact)
            db.flush()
            legacy = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Legacy Name",
                company="Legacy Co",
                contact_email="legacy@example.com",
            )
            fresh = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Fresh Name",
                company="Fresh Co",
                contact_email="fresh@example.com",
            )
            db.add_all([legacy, fresh])
            db.flush()
            contact.active_recruiter_lead_id = legacy.id
            db.commit()
            contact_id, legacy_id, fresh_id = contact.id, legacy.id, fresh.id

        deleted = self.client.delete(f"/recruiter-numbers/{contact_id}/versions/{legacy_id}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        with Session(self.engine) as db:
            contact = db.get(PremiumNumberContact, contact_id)
            self.assertEqual(contact.active_recruiter_lead_id, fresh_id)
            self.assertEqual(contact.recruiter_name, "Fresh Name")
            self.assertEqual(contact.recruiter_email, "fresh@example.com")
            self.assertIsNone(db.get(PremiumNumberLead, legacy_id))

    def test_delete_only_version_is_rejected(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550103",
                display_phone_number="(214) 555-0103",
                is_recruiter=True,
                recruiter_name="Solo Name",
            )
            db.add(contact)
            db.flush()
            only = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                owner_name="Solo Name",
                company="Solo Co",
            )
            db.add(only)
            db.flush()
            contact.active_recruiter_lead_id = only.id
            db.commit()
            contact_id, only_id = contact.id, only.id

        deleted = self.client.delete(f"/recruiter-numbers/{contact_id}/versions/{only_id}")
        self.assertEqual(deleted.status_code, 400, deleted.text)
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(PremiumNumberLead, only_id))

    def test_patch_recruiter_number_updates_manual_fields(self) -> None:
        with Session(self.engine) as db:
            contact = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550300",
                display_phone_number="(214) 555-0300",
                recruiter_name="Unknown",
                company="Unknown",
                designation="Unknown",
                recruiter_email="",
            )
            db.add(contact)
            db.commit()
            contact_id = contact.id

        response = self.client.patch(
            f"/recruiter-numbers/{contact_id}",
            json={
                "recruiter_name": "Priya Sharma",
                "company": "Acme Staffing",
                "designation": "Technical Recruiter",
                "recruiter_email": "priya@acmestaffing.example",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["recruiter_name"], "Priya Sharma")
        self.assertEqual(payload["company"], "Acme Staffing")
        self.assertEqual(payload["designation"], "Technical Recruiter")
        self.assertEqual(payload["recruiter_email"], "priya@acmestaffing.example")
        with Session(self.engine) as db:
            contact = db.get(PremiumNumberContact, contact_id)
            self.assertEqual(contact.recruiter_name, "Priya Sharma")
            self.assertEqual(contact.company, "Acme Staffing")

    def test_patch_employer_number_updates_manual_fields(self) -> None:
        with Session(self.engine) as db:
            contact = EmployerNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550400",
                display_phone_number="(214) 555-0400",
                owner_name="Unknown",
                company="Unknown",
            )
            db.add(contact)
            db.commit()
            contact_id = contact.id

        response = self.client.patch(
            f"/employer-numbers/{contact_id}",
            json={"owner_name": "Bvishnu Reddy", "company": "KommForce Solutions"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["owner_name"], "Bvishnu Reddy")
        self.assertEqual(payload["company"], "KommForce Solutions")
        with Session(self.engine) as db:
            contact = db.get(PremiumNumberContact, contact_id)
            self.assertEqual(contact.owner_name, "Bvishnu Reddy")

    def test_patch_recruiter_number_rejects_employer_only_contact(self) -> None:
        with Session(self.engine) as db:
            contact = EmployerNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550500",
                display_phone_number="(214) 555-0500",
                owner_name="Someone",
                company="Some Co",
            )
            db.add(contact)
            db.commit()
            contact_id = contact.id

        response = self.client.patch(f"/recruiter-numbers/{contact_id}", json={"recruiter_name": "New Name"})
        self.assertEqual(response.status_code, 404, response.text)

    def test_patch_opportunity_updates_new_fields_and_keeps_live_linkedin(self) -> None:
        with Session(self.engine) as db:
            contact = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550200",
                display_phone_number="(214) 555-0200",
                recruiter_name="Recruiter",
                company="Agency",
                designation="Recruiter",
                recruiter_email="recruiter@agency.example",
                linkedin_url="https://linkedin.com/in/recruiter",
            )
            db.add(contact)
            db.flush()
            opportunity = RecruiterOpportunity(
                owner_id=main.settings.owner_id,
                recruiter_number_id=contact.id,
                source_email_id=44,
                gmail_message_id="gmail-opportunity-edit",
                email_subject="Original subject",
                email_sender="sender@agency.example",
                gmail_open_url="",
                job_title="Developer",
                end_client="Old Client",
                location="Dallas",
                work_mode="Remote",
                visa_restrictions="",
                extracted_skills="Python",
                evidence="test",
                status="New",
                notes="",
            )
            db.add(opportunity)
            db.flush()
            lineage = opportunity_lineage_service.create_lineage(
                db,
                owner_id=main.settings.owner_id,
                origin_type="gmail",
                source_type="gmail",
                external_id="",
                source_url="",
                process_name="test_fixture",
                recruiter_opportunity_id=opportunity.id,
            )
            db.commit()
            opportunity_id = opportunity.id
            lineage_id = lineage.id

        response = self.client.patch(
            f"/recruiter-opportunities/{opportunity_id}",
            json={
                "job_title": "Senior Developer",
                "resume_file_name": "senior-python.pdf",
                "implementation_partner": "Partner Co",
                "prime_vendor": "Prime Co",
                "end_client": "New Client",
                "domain": "Healthcare",
                "extracted_skills": "Python, AWS",
                "status": "Closed",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["email_id"], 44)
        self.assertEqual(payload["end_client"], "New Client")
        self.assertEqual(payload["prime_vendor"], "Prime Co")
        self.assertEqual(payload["linkedin_url"], "https://linkedin.com/in/recruiter")
        with Session(self.engine) as db:
            lineage = db.get(OpportunityLineage, lineage_id)
            self.assertEqual(lineage.current_status, "closed")
            self.assertIsNotNone(lineage.closed_at)
            event = (
                db.query(OpportunityLifecycleEvent)
                .filter_by(lineage_id=lineage_id, event_type="status_changed")
                .one()
            )
            self.assertEqual(event.process_name, "main_api")
            self.assertIn('"to":"Closed"', event.metadata_json)

    def test_bulk_rescore_calls_shared_workflow_once_per_source_email(self) -> None:
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="sender@agency.example",
                subject="Role",
                body="Call (214) 555-0300 and (214) 555-0301",
                role="Developer",
                location="Dallas",
                salary_text="",
                skills_text="Python",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="gmail-bulk-rescore",
                external_thread_id="thread-bulk-rescore",
            )
            db.add(email)
            db.flush()
            rows = [
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=email.id,
                    normalized_phone_number=f"1214555030{suffix}",
                    display_phone_number=f"(214) 555-030{suffix}",
                    owner_name="Unknown",
                    company="Unknown",
                    designation="Unknown",
                    state="pending",
                )
                for suffix in (0, 1)
            ]
            db.add_all(rows)
            db.commit()
            review_ids = [row.id for row in rows]

        capture = Mock(return_value=SimpleNamespace())
        with patch(
            "app.main._get_candidate_runtime_service",
            return_value=SimpleNamespace(capture_premium_numbers=capture),
        ):
            response = self.client.post(
                "/number-review/bulk-rescore",
                json={"review_ids": review_ids},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(
            [item["status"] for item in response.json()["results"]],
            ["pending", "pending"],
        )

    def test_bulk_rescore_promotes_cleared_threshold_leads(self) -> None:
        # Unlike test_bulk_rescore_calls_shared_workflow_once_per_source_email above,
        # this exercises the real capture_premium_numbers -> _run() path end to end --
        # nothing about promotion/state-transition is mocked. The only thing patched
        # is extract_phone_leads itself (same idiom already used by
        # test_reextract_is_idempotent_for_leads_and_opportunities elsewhere in this
        # file), so the test doesn't depend on a live DeepSeek AI call: backend/.env
        # has a real Deepseek_API_KEY configured, so leaving extract_phone_leads
        # fully unmocked would make this test perform a real network call to the
        # DeepSeek API (slow, non-deterministic, and unavailable in CI/sandboxed
        # environments) before ever falling back to the deterministic regex path.
        # Every downstream step -- the existing-review idempotency check, the
        # auto-routing threshold re-evaluation, contact creation, version linking,
        # opportunity creation, and the review row's state transition -- runs for
        # real against the fixture lead below.
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="sender@agency.example",
                subject="Java role",
                body="Call Priya Sharma at (214) 555-0400 for this role",
                role="Java Developer",
                location="Dallas",
                salary_text="",
                skills_text="Java",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="gmail-bulk-rescore-promote",
                external_thread_id="thread-bulk-rescore-promote",
                gmail_received_at=now,
            )
            db.add(email)
            db.flush()
            row = NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=email.id,
                normalized_phone_number="12145550400",
                display_phone_number="(214) 555-0400",
                owner_name="Unknown",
                company="Unknown",
                designation="Unknown",
                email_subject="Java role",
                email_sender="sender@agency.example",
                state="pending",
            )
            db.add(row)
            db.commit()
            review_id = row.id
            email_id = email.id

        cleared_lead = ExtractedContactGroup(
            phone_number_display="(214) 555-0400",
            phone_number_normalized="12145550400",
            owner_name="Priya Sharma",
            contact_email="priya@agency.example",
            company="Agency Co",
            designation="Senior Recruiter",
            purpose="Recruiter direct number",
            confidence="high",
            contact_type="recruiter_direct",
            recruiter_relevance_score=90,
            is_recruiter_relevant=True,
            relevance_reason="external_domain,purpose_positive",
            source_fragment="Call Priya Sharma at (214) 555-0400 for this role",
            role="recruiter",
            extraction_source="ai",
        )
        with patch(
            "app.services.phone_intelligence_workflow_service.extract_phone_leads",
            return_value=[cleared_lead],
        ):
            response = self.client.post(
                "/number-review/bulk-rescore",
                json={"review_ids": [review_id]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["results"],
            [{"review_id": review_id, "status": "classified_recruiter"}],
        )

        with Session(self.engine) as db:
            refreshed = db.get(NumberReviewQueue, review_id)
            self.assertEqual(refreshed.state, "classified_recruiter")

            contact = (
                db.query(PremiumNumberContact)
                .filter(
                    PremiumNumberContact.owner_id == main.settings.owner_id,
                    PremiumNumberContact.normalized_phone_number == "12145550400",
                )
                .first()
            )
            self.assertIsNotNone(contact)
            self.assertTrue(contact.is_recruiter)
            self.assertIsNotNone(contact.active_recruiter_lead_id)

            lead = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertIsNotNone(lead)
            self.assertEqual(lead.recruiter_email_id, email_id)
            self.assertEqual(lead.owner_name, "Priya Sharma")
            self.assertEqual(lead.phone_number_normalized, "12145550400")

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
                db.query(PremiumNumberContact)
                .filter(
                    PremiumNumberContact.owner_id == main.settings.owner_id,
                    PremiumNumberContact.normalized_phone_number == "18322713861",
                )
                .first()
            )
            self.assertIsNotNone(recruiter)
            assert recruiter is not None
            self.assertEqual(recruiter.recruiter_name, "Samshritha Gangula")


    def test_inventory_filters_surface_flagged_contacts_only_when_requested(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            active = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550101",
                display_phone_number="+1 (214) 555-0101",
                recruiter_name="Active Recruiter",
                company="Agency",
                designation="Recruiter",
                recruiter_email="active@agency.example",
                source_type="gmail",
                source_id=101,
                created_at=now,
                updated_at=now,
            )
            flagged = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="nvoids-placeholder-101",
                display_phone_number="Unknown",
                recruiter_name="Unknown",
                company="Unknown",
                designation="Unknown",
                recruiter_email="",
                source_type="nvoids",
                source_id=202,
                created_at=now,
                updated_at=now,
            )
            db.add_all([active, flagged])
            db.commit()

        default_response = self.client.get("/recruiter-numbers")
        self.assertEqual(default_response.status_code, 200, default_response.text)
        self.assertEqual([row["recruiter_name"] for row in default_response.json()["items"]], ["Active Recruiter"])

        flagged_response = self.client.get("/recruiter-numbers?flagged=true&source_type=nvoids")
        self.assertEqual(flagged_response.status_code, 200, flagged_response.text)
        self.assertEqual(len(flagged_response.json()["items"]), 1)
        self.assertEqual(flagged_response.json()["items"][0]["status"], "Flagged")
        self.assertTrue(flagged_response.json()["items"][0]["flagged"])

        active_response = self.client.get("/recruiter-numbers?flagged=false&source_type=gmail")
        self.assertEqual(active_response.status_code, 200, active_response.text)
        self.assertEqual(len(active_response.json()["items"]), 1)
        self.assertEqual(active_response.json()["items"][0]["status"], "Active")

    def test_unknown_name_or_company_flags_contact_even_with_valid_phone(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            unknown_name = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550301",
                display_phone_number="+1 (214) 555-0301",
                recruiter_name="Unknown",
                company="Agency",
                designation="Recruiter",
                recruiter_email="recruiter@agency.example",
                source_type="gmail",
                source_id=301,
                created_at=now,
                updated_at=now,
            )
            unknown_company = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550302",
                display_phone_number="+1 (214) 555-0302",
                recruiter_name="Real Recruiter",
                company="Unknown",
                designation="Recruiter",
                recruiter_email="recruiter2@agency.example",
                source_type="gmail",
                source_id=302,
                created_at=now,
                updated_at=now,
            )
            unknown_owner = EmployerNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550303",
                display_phone_number="+1 (214) 555-0303",
                owner_name="",
                company="Agency",
                source_type="gmail",
                source_id=303,
                created_at=now,
                updated_at=now,
            )
            db.add_all([unknown_name, unknown_company, unknown_owner])
            db.commit()

        default_recruiters = self.client.get("/recruiter-numbers")
        self.assertEqual(default_recruiters.status_code, 200, default_recruiters.text)
        self.assertEqual(default_recruiters.json()["items"], [])

        flagged_recruiters = self.client.get("/recruiter-numbers?flagged=true")
        self.assertEqual(flagged_recruiters.status_code, 200, flagged_recruiters.text)
        self.assertEqual(len(flagged_recruiters.json()["items"]), 2)
        self.assertTrue(all(item["flagged"] and item["status"] == "Flagged" for item in flagged_recruiters.json()["items"]))

        flagged_employers = self.client.get("/employer-numbers?flagged=true")
        self.assertEqual(flagged_employers.status_code, 200, flagged_employers.text)
        self.assertEqual(len(flagged_employers.json()["items"]), 1)
        self.assertTrue(flagged_employers.json()["items"][0]["flagged"])

    def test_unresolved_dual_role_unverified_contact_is_flagged(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550401",
                display_phone_number="+1 (214) 555-0401",
                is_recruiter=True,
                is_employer=True,
                recruiter_verification_level="unverified",
                recruiter_name="Some Recruiter",
                owner_name="Some Employer",
                company="Some Co",
                recruiter_email="recruiter@agency.example",
                source_type="gmail",
                source_id=401,
                created_at=now,
                updated_at=now,
            )
            db.add(contact)
            db.commit()

        flagged_recruiters = self.client.get("/recruiter-numbers?flagged=true")
        self.assertEqual(flagged_recruiters.status_code, 200, flagged_recruiters.text)
        self.assertEqual(len(flagged_recruiters.json()["items"]), 1)
        self.assertEqual(flagged_recruiters.json()["items"][0]["status"], "Flagged")

        flagged_employers = self.client.get("/employer-numbers?flagged=true")
        self.assertEqual(flagged_employers.status_code, 200, flagged_employers.text)
        self.assertEqual(len(flagged_employers.json()["items"]), 1)
        self.assertEqual(flagged_employers.json()["items"][0]["status"], "Flagged")

    def test_unknown_designation_alone_is_not_flagged_and_falls_back_by_domain(self) -> None:
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
            outside_domain = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550401",
                display_phone_number="+1 (214) 555-0401",
                recruiter_name="Real Recruiter",
                company="Agency",
                designation="Unknown",
                recruiter_email="recruiter@agency.example",
                source_type="gmail",
                source_id=401,
                created_at=now,
                updated_at=now,
            )
            employer_domain = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550402",
                display_phone_number="+1 (214) 555-0402",
                recruiter_name="Internal Contact",
                company="Horizonsoftech",
                designation="Unknown",
                recruiter_email="internal@horizonsoftech.net",
                source_type="gmail",
                source_id=402,
                created_at=now,
                updated_at=now,
            )
            db.add_all([outside_domain, employer_domain])
            db.commit()

        response = self.client.get("/recruiter-numbers")
        self.assertEqual(response.status_code, 200, response.text)
        items = {item["recruiter_email"]: item for item in response.json()["items"]}
        self.assertEqual(len(items), 2)
        self.assertFalse(items["recruiter@agency.example"]["flagged"])
        self.assertEqual(items["recruiter@agency.example"]["designation"], "Recruiter")
        self.assertFalse(items["internal@horizonsoftech.net"]["flagged"])
        self.assertEqual(items["internal@horizonsoftech.net"]["designation"], "Unknown")

    def test_bulk_contact_soft_delete_is_owner_scoped_and_preserves_opportunities(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            owned = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550202",
                display_phone_number="+1 (214) 555-0202",
                recruiter_name="Owned Recruiter",
                company="Agency",
                designation="Recruiter",
                recruiter_email="owned@agency.example",
                source_type="gmail",
                source_id=1,
                created_at=now,
                updated_at=now,
            )
            foreign = RecruiterNumber(
                owner_id="another-owner",
                normalized_phone_number="12145550303",
                display_phone_number="+1 (214) 555-0303",
                recruiter_name="Foreign Recruiter",
                company="Agency",
                designation="Recruiter",
                recruiter_email="foreign@agency.example",
                created_at=now,
                updated_at=now,
            )
            db.add_all([owned, foreign])
            db.flush()
            db.add(
                RecruiterOpportunity(
                    owner_id=main.settings.owner_id,
                    recruiter_number_id=owned.id,
                    source_email_id=None,
                    gmail_message_id="soft-delete-opportunity",
                    source_type="gmail",
                    email_subject="Role",
                    email_sender="owned@agency.example",
                    gmail_open_url="",
                    job_title="Engineer",
                    end_client="Client",
                    location="Remote",
                    work_mode="Remote",
                    visa_restrictions="",
                    extracted_skills="Python",
                    evidence="",
                    status="New",
                    notes="",
                    created_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            owned_id, foreign_id = owned.id, foreign.id

        response = self.client.post(
            "/recruiter-numbers/bulk-delete",
            json={"contact_ids": [owned_id, foreign_id, 999999]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["status"] for item in response.json()["results"]],
            ["deleted", "not_found", "not_found"],
        )
        self.assertEqual(self.client.get("/recruiter-numbers?flagged=false").json()["items"], [])
        with Session(self.engine) as db:
            self.assertIsNotNone(db.get(PremiumNumberContact, owned_id).deleted_at)
            self.assertEqual(
                db.query(RecruiterOpportunity).filter(
                    RecruiterOpportunity.recruiter_number_id == owned_id
                ).count(),
                1,
            )

    def test_bulk_role_change_and_pending_count(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            contact = EmployerNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550404",
                display_phone_number="+1 (214) 555-0404",
                owner_name="Hiring Desk",
                company="Employer",
                source_type="gmail",
                source_id=44,
                created_at=now,
                updated_at=now,
            )
            db.add(contact)
            db.add_all([
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=None,
                    normalized_phone_number=f"12145550{index}",
                    display_phone_number=f"+1 (214) 555-0{index}",
                    owner_name="Unknown",
                    company="Unknown",
                    designation="Unknown",
                    confidence="low",
                    purpose="Unknown",
                    evidence_snippet="",
                    email_subject="",
                    email_sender="",
                    gmail_open_url="",
                    state=state,
                    created_at=now,
                    updated_at=now,
                )
                for index, state in ((505, "pending"), (606, "dismissed"))
            ])
            db.commit()
            contact_id = contact.id

        response = self.client.post(
            "/employer-numbers/bulk-mark-recruiter",
            json={"contact_ids": [contact_id]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"][0]["status"], "marked_recruiter")
        self.assertEqual(self.client.get("/number-review/pending-count").json(), {"count": 1})
        with Session(self.engine) as db:
            changed = db.get(PremiumNumberContact, contact_id)
            self.assertTrue(changed.is_recruiter)
            self.assertTrue(changed.is_employer)

    def test_bulk_contact_rescore_uses_stored_gmail_source_and_bumps_last_checked(self) -> None:
        now = datetime.now(UTC)
        old = now.replace(year=now.year - 1)
        with Session(self.engine) as db:
            email = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="rescore@example.com",
                subject="Role",
                body="Call +1 214 555 0707",
                role="Developer",
                location="Remote",
                salary_text="",
                skills_text="Python",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="Thanks",
                source="gmail",
                external_message_id="rescore-contact-message",
                external_thread_id="rescore-contact-thread",
                gmail_received_at=now,
                recipient_email="to@example.com",
                cc_email="cc@example.com",
            )
            db.add(email)
            db.flush()
            contact = RecruiterNumber(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145550707",
                display_phone_number="+1 (214) 555-0707",
                recruiter_name="Rescore Recruiter",
                company="Agency",
                designation="Recruiter",
                recruiter_email="rescore@example.com",
                first_detected_email_id=email.id,
                source_type="gmail",
                source_id=email.id,
                updated_at=old,
                created_at=old,
            )
            db.add(contact)
            db.flush()
            lead = PremiumNumberLead(
                owner_id=main.settings.owner_id,
                recruiter_email_id=email.id,
                contact_id=contact.id,
                phone_number_normalized=contact.normalized_phone_number,
                phone_number_display=contact.display_phone_number,
                role="recruiter",
                extraction_source="ai",
                contact_email=contact.recruiter_email,
                owner_name=contact.recruiter_name,
                company=contact.company,
                designation=contact.designation,
                purpose="Recruiter contact",
                confidence="high",
                contact_type="recruiter_direct",
                recruiter_relevance_score=88,
                is_recruiter_relevant=True,
                relevance_reason="relevant",
                source_fragment="call me",
                source_email_sender=email.sender,
                source_email_subject=email.subject,
                source_email_message_id=email.external_message_id,
            )
            db.add(lead)
            db.flush()
            contact.active_recruiter_lead_id = lead.id
            db.commit()
            contact_id, email_id = contact.id, email.id

        runtime = Mock()
        captured_email_ids: list[int] = []
        runtime.capture_premium_numbers.side_effect = lambda _db, source: (
            captured_email_ids.append(source.id) or SimpleNamespace(stored_count=1)
        )
        with patch.object(main, "_get_candidate_runtime_service", return_value=runtime):
            response = self.client.post(
                "/recruiter-numbers/bulk-rescore",
                json={"contact_ids": [contact_id]},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["results"], [{"contact_id": contact_id, "status": "rescored"}])
        runtime.capture_premium_numbers.assert_called_once()
        self.assertEqual(captured_email_ids, [email_id])
        with Session(self.engine) as db:
            refreshed = db.get(PremiumNumberContact, contact_id)
            self.assertGreater(refreshed.updated_at, old)
            self.assertEqual(refreshed.source_type, "gmail")
            self.assertEqual(refreshed.source_id, email_id)

    def test_partial_digit_search_covers_all_number_lists(self) -> None:
        with Session(self.engine) as db:
            db.add(PremiumNumberLead(
                owner_id=main.settings.owner_id,
                phone_number_normalized="12145551212",
                phone_number_display="(214) 555-1212",
                owner_name="Ada",
                company="Example",
                designation="Recruiter",
                is_recruiter_relevant=True,
            ))
            db.add(NumberReviewQueue(
                owner_id=main.settings.owner_id,
                source_email_id=9,
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                state="pending",
            ))
            db.add(PremiumNumberContact(
                owner_id=main.settings.owner_id,
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                is_employer=True,
                recruiter_name="Ada",
                owner_name="Hiring Desk",
                company="Example",
                designation="Recruiter",
                recruiter_email="ada@example.com",
                employer_email="hr@example.com",
                recruiter_verification_level="verified",
            ))
            db.commit()

        for query in ("12145551212", "55512", "(214) 555"):
            for path in ("/premium-numbers", "/number-review", "/recruiter-numbers", "/employer-numbers"):
                response = self.client.get(path, params={"q": query})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()["items"]), 1, (query, path))

    def test_extraction_audit_is_owner_scoped_and_filterable(self) -> None:
        with Session(self.engine) as db:
            db.add_all([
                PremiumNumberExtractionAudit(
                    owner_id=main.settings.owner_id,
                    source_email_id=42,
                    raw_value="(214) 555-1212",
                    normalized_value="12145551212",
                    status="accepted",
                    stage="accepted",
                    reason="candidate_accepted",
                ),
                PremiumNumberExtractionAudit(
                    owner_id="other-owner",
                    source_email_id=42,
                    raw_value="(469) 555-1212",
                    normalized_value="14695551212",
                    status="rejected",
                    stage="sbert",
                    reason="noise",
                ),
            ])
            db.commit()

        response = self.client.get(
            "/premium-numbers/extraction-audit",
            params={"source_email_id": 42, "status": "accepted"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(response.json()["items"][0]["reason"], "candidate_accepted")
        invalid = self.client.get("/premium-numbers/extraction-audit")
        self.assertEqual(invalid.status_code, 422)


if __name__ == "__main__":
    unittest.main()
