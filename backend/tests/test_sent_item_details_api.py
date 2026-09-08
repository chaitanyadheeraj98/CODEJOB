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
from app.models import (
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
)


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
        self.assertEqual(payload["recruiter_email_domain"], "example.com")
        self.assertEqual(payload["recruiter_phone"], None)

    def test_employer_domain_recipient_resolves_employer_fields_and_blanks_recruiter_email(self) -> None:
        with Session(self.engine) as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="HR Desk <hr@horizonsofttech.net>",
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
                external_message_id="msg-employer-1",
                external_thread_id="thread-employer-1",
                recipient_email="hr@horizonsofttech.net",
                routing_status="safe",
                routing_confidence=0.9,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                resume_file_name="resume.pdf",
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-employer-1",
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.flush()
            email_id = row.id
            db.add(
                PremiumNumberContact(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12145550401",
                    display_phone_number="+1 (214) 555-0401",
                    is_employer=True,
                    owner_name="HR Desk",
                    employer_email="hr@horizonsofttech.net",
                    first_detected_email_id=email_id,
                )
            )
            db.commit()

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsNone(payload["recruiter_email"])
        self.assertIsNone(payload["recruiter_email_domain"])
        self.assertEqual(payload["employer_name"], "HR Desk")
        self.assertEqual(payload["employer_email"], "hr@horizonsofttech.net")
        self.assertEqual(payload["employer_phone"], "+1 (214) 555-0401")

    def test_employer_domain_recipient_with_no_matching_contact_falls_back_to_guess(self) -> None:
        with Session(self.engine) as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="HR Desk <hr@horizonsofttech.net>",
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
                external_message_id="msg-employer-2",
                external_thread_id="thread-employer-2",
                recipient_email="hr@horizonsofttech.net",
                routing_status="safe",
                routing_confidence=0.9,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                resume_file_name="resume.pdf",
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-employer-2",
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.commit()
            email_id = row.id

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIsNone(payload["recruiter_email"])
        self.assertIsNone(payload["employer_name"])
        self.assertEqual(payload["employer_email"], "hr@horizonsofttech.net")
        self.assertIsNone(payload["employer_phone"])

    def _forwarded_requirement(self, **overrides: object) -> int:
        """A requirement forwarded by one firm to a recruiter at another.

        `company` is the extractor's word for the firm that *sent* the mail, so it
        describes the forwarder and says nothing about the recruiter downstream.
        """
        defaults = dict(
            owner_id=main.settings.owner_id,
            sender="Alekya <alekya@rpatechnologyinc.com>",
            subject="Jr. Java Full stack Developer",
            body="Java, Spring Boot",
            company="RPATECHNOLOGY INC",
            role="Jr. Java Full stack Developer",
            recipient_email="lalitha.y@metasisinfo.com",
            state="approved_sent",
            sent_status="sent",
            source="gmail",
            external_message_id="msg-forwarded-1",
            gmail_sent_id="sent-forwarded-1",
            sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
        )
        defaults.update(overrides)
        with Session(self.engine) as db:
            row = RecruiterEmail(**defaults)
            db.add(row)
            db.commit()
            return row.id

    def test_a_contact_made_for_the_recipient_does_not_inherit_the_senders_company(self) -> None:
        email_id = self._forwarded_requirement()

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["recruiter_email"], "lalitha.y@metasisinfo.com")
        # The panel used to read "RPATECHNOLOGY INC" here, and the contact created
        # behind it was filed under that firm permanently. 150 of the 394 recruiter
        # contacts carried a company copied across a domain boundary this way.
        self.assertIsNone(payload["recruiter_company"])

        # The contact the panel creates behind the response is the durable damage:
        # once filed under the wrong firm it stays there. A GET does not commit, so
        # resolve in a session of our own to see what it would have written.
        with Session(self.engine) as db:
            email = db.query(RecruiterEmail).filter(RecruiterEmail.id == email_id).one()
            _, contact = main._resolve_recruiter_contact_for_email(db, email)
            db.commit()
            assert contact is not None
            self.assertEqual(contact.recruiter_email, "lalitha.y@metasisinfo.com")
            self.assertEqual(contact.company, "Unknown")

    def test_a_contact_made_for_the_sender_keeps_the_company_the_mail_stated(self) -> None:
        email_id = self._forwarded_requirement(recipient_email=None, external_message_id="msg-direct-1")

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recruiter_company"], "RPATECHNOLOGY INC")

    def _metasis_colleague(self) -> None:
        """A contact who has named the firm behind metasisinfo.com."""
        with Session(self.engine) as db:
            db.add(
                PremiumNumberContact(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12485550100",
                    display_phone_number="+1 248 555 0100",
                    is_recruiter=True,
                    recruiter_name="Ravi Kumar",
                    recruiter_email="ravi@metasisinfo.com",
                    recruiter_email_domain="metasisinfo.com",
                    company="Metasis Information Systems LLC",
                )
            )
            db.commit()

    def test_the_requirement_company_is_the_recruiters_never_the_forwarders(self) -> None:
        self._metasis_colleague()
        email_id = self._forwarded_requirement(
            external_message_id="msg-forwarded-2", gmail_sent_id="sent-forwarded-2"
        )

        payload = self.client.get(f"/candidates/{email_id}/sent-details").json()
        # The Requirement card's "Company" and the Recruiter card's "Recruiter
        # Company" are one fact under two labels, and used to disagree: "Company"
        # ran on past the recruiter to the employer's own lead and printed
        # RPATECHNOLOGY INC, the firm that forwarded the mail.
        self.assertEqual(payload["company"], "Metasis Information Systems LLC")
        self.assertEqual(payload["recruiter_company"], payload["company"])
        self.assertNotEqual(payload["company"], "RPATECHNOLOGY INC")

    def test_an_opportunity_end_client_is_reported_as_a_client_not_as_a_company(self) -> None:
        self._metasis_colleague()
        email_id = self._forwarded_requirement(
            external_message_id="msg-forwarded-3", gmail_sent_id="sent-forwarded-3"
        )
        with Session(self.engine) as db:
            recruiter = db.query(PremiumNumberContact).first()
            assert recruiter is not None
            db.add(
                RecruiterOpportunity(
                    owner_id=main.settings.owner_id,
                    recruiter_number_id=recruiter.id,
                    gmail_message_id="opportunity-forwarded-3",
                    source_email_id=email_id,
                    job_title="Jr. Java Full stack Developer",
                    end_client="State of New Jersey",
                )
            )
            db.commit()

        payload = self.client.get(f"/candidates/{email_id}/sent-details").json()
        self.assertEqual(payload["end_client"], "State of New Jersey")
        self.assertEqual(payload["company"], "Metasis Information Systems LLC")

    def test_employer_signature_lead_does_not_leak_into_recruiter_name_or_phone(self) -> None:
        with Session(self.engine) as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Asiya Shaik <asiya@horizonsofttech.net>",
                subject="Java Tech Lead",
                body="Kindly share resume at aniket.chaturvedi@pacerstaffing.com\n\nAsiya Shaik\nPh : (248) 237-3497",
                role="Java Tech Lead",
                location="Remote",
                salary_text="",
                skills_text="Java",
                score=88,
                decision="Qualified",
                state="approved_sent",
                draft_reply="Hi",
                approval_status="approved",
                sent_status="sent",
                source="gmail",
                external_message_id="msg-mixed-role-1",
                external_thread_id="thread-mixed-role-1",
                recipient_email="aniket.chaturvedi@pacerstaffing.com",
                cc_email="asiya@horizonsofttech.net",
                routing_status="safe",
                routing_confidence=0.9,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                resume_file_name="resume.pdf",
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-mixed-role-1",
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.flush()
            email_id = row.id
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=email_id,
                    phone_number_normalized="12482373497",
                    phone_number_display="(248) 237-3497",
                    role="employer",
                    contact_email="asiya@horizonsofttech.net",
                    owner_name="Asiya Shaik",
                    company="Horizons of Tech",
                    designation="Unknown",
                    purpose="Employer contact",
                    confidence="high",
                    contact_type="employer",
                    recruiter_relevance_score=15,
                    is_recruiter_relevant=False,
                    relevance_reason="employer_domain",
                    extraction_source="ai",
                    source_fragment="",
                    source_email_sender="",
                    source_email_subject="",
                )
            )
            db.commit()

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["recruiter_email"], "aniket.chaturvedi@pacerstaffing.com")
        self.assertIsNone(payload["recruiter_name"])
        self.assertIsNone(payload["recruiter_phone"])

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

    def test_nvoids_sent_details_requirement_link_falls_back_to_message_id(self) -> None:
        with Session(self.engine) as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="nvoids@example.com",
                subject="Senior Java Developer",
                body="Vendor: Example Vendor",
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
                external_message_id="nvoids:4471002",
                external_thread_id="nvoids:4471002",
                recipient_email="nvoids@example.com",
                cc_email="employer@example.com",
                routing_status="safe",
                routing_confidence=0.95,
                routing_reason="ok",
                routing_evidence="[]",
                routing_candidates="[]",
                routing_confirmed=True,
                sent_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
                gmail_sent_id="sent-nvoids-2",
                created_at=datetime(2026, 6, 27, 17, 0, tzinfo=UTC),
                updated_at=datetime(2026, 6, 27, 18, 0, tzinfo=UTC),
            )
            db.add(row)
            db.commit()
            email_id = row.id

        response = self.client.get(f"/candidates/{email_id}/sent-details")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["requirement_received_link"], "https://nvoids.com/job_details.jsp?id=4471002")


if __name__ == "__main__":
    unittest.main()
