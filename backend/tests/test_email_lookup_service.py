import os
import unittest
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    EmailConversation,
    NumberReviewQueue,
    PremiumNumberLead,
    PremiumNumberContact,
    RecentRun,
    RecentRunSkippedItem,
    RecruiterEmail,
    RecruiterOpportunity,
)


def RecruiterNumber(**values):
    return PremiumNumberContact(is_recruiter=True, **values)


def EmployerNumber(**values):
    return PremiumNumberContact(is_employer=True, **values)


class EmailLookupApiTests(unittest.TestCase):
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

    def _add_email(
        self,
        db: Session,
        *,
        sender: str,
        state: str = "needs_review",
        owner_id: str | None = None,
        sync_batch_id: str | None = None,
        suffix: str,
        occurred_at: datetime | None = None,
    ) -> RecruiterEmail:
        now = occurred_at or datetime.now(UTC)
        row = RecruiterEmail(
            owner_id=owner_id or main.settings.owner_id,
            sender=sender,
            subject=f"Role {suffix}",
            body="Body",
            state=state,
            decision=state,
            source="gmail",
            external_message_id=f"lookup-{suffix}",
            external_thread_id=f"lookup-thread-{suffix}",
            recipient_email=f"to-{suffix}@example.com",
            cc_email=f"cc-{suffix}@example.com",
            sync_batch_id=sync_batch_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        return row

    def test_sender_match_returns_visible_and_hidden_candidate_states(self) -> None:
        with Session(self.engine) as db:
            for index, state in enumerate(("needs_review", "failed", "approved_sent", "rejected")):
                self._add_email(
                    db,
                    sender=f"Trace Recruiter <trace-{index}@example.com>",
                    state=state,
                    suffix=f"states-{index}",
                )
            db.commit()

        response = self.client.get("/search/email", params={"q": "trace-"})

        self.assertEqual(response.status_code, 200, response.text)
        anchor_hits = [hit for hit in response.json()["hits"] if hit["section"] != "recent_runs"]
        self.assertEqual(
            {(hit["state"], hit["section"]) for hit in anchor_hits},
            {
                ("needs_review", "needs_review"),
                ("failed", "failed_mapping"),
                ("approved_sent", "sent_items"),
                ("rejected", "other"),
            },
        )

    def test_recruiter_email_matches_by_subject_role_and_skills_text(self) -> None:
        with Session(self.engine) as db:
            subject_email = self._add_email(db, sender="Subject Recruiter <subject@example.com>", suffix="subject-match")
            subject_email.subject = "Lead Java Developer opening"
            role_email = self._add_email(db, sender="Role Recruiter <role@example.com>", suffix="role-match")
            role_email.role = "Java Developer"
            skills_email = self._add_email(db, sender="Skills Recruiter <skills@example.com>", suffix="skills-match")
            skills_email.skills_text = "Java, Spring Boot"
            db.commit()
            subject_id, role_id, skills_id = subject_email.id, role_email.id, skills_email.id

        by_subject_or_role = self.client.get("/search/email", params={"q": "Java Developer"})
        by_skills = self.client.get("/search/email", params={"q": "Spring Boot"})

        self.assertEqual(
            {hit["recruiter_email_id"] for hit in by_subject_or_role.json()["hits"]},
            {subject_id, role_id},
        )
        self.assertEqual(by_skills.json()["hits"][0]["recruiter_email_id"], skills_id)

    def test_recent_run_skipped_item_matches_by_title_or_subject(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecentRunSkippedItem(
                    owner_id=main.settings.owner_id,
                    run_source="gmail_sync",
                    run_key="gmail_sync:title-match",
                    source_type="gmail",
                    outcome="skipped",
                    reason_code="intent_rejected",
                    reason_detail="Rejected before candidate creation",
                    candidate_email_id=None,
                    title_or_subject="Staff Python Engineer",
                    sender="Skipped Recruiter <title-only@example.com>",
                )
            )
            db.commit()

        response = self.client.get("/search/email", params={"q": "Python Engineer"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["hits"]), 1)
        self.assertEqual(response.json()["hits"][0]["section"], "recent_runs")

    def test_numeric_query_matches_exact_recruiter_email_id(self) -> None:
        with Session(self.engine) as db:
            row = self._add_email(db, sender="Numeric Recruiter <numeric@example.com>", suffix="numeric")
            db.commit()
            email_id = row.id

        response = self.client.get("/search/email", params={"q": str(email_id)})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["hits"][0]["recruiter_email_id"], email_id)

    def test_one_email_returns_all_satellite_records(self) -> None:
        now = datetime.now(UTC)
        with Session(self.engine) as db:
            email = self._add_email(
                db,
                sender="Everywhere Recruiter <everywhere@example.com>",
                state="approved_sent",
                sync_batch_id="batch-everywhere",
                suffix="everywhere",
                occurred_at=now,
            )
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=email.id,
                    phone_number_normalized="12145551212",
                    phone_number_display="(214) 555-1212",
                    owner_name="Everywhere Recruiter",
                    company="Everywhere Inc",
                    designation="Recruiter",
                    purpose="Recruiter direct number",
                    confidence="high",
                    source_fragment="Call me",
                    source_email_sender=email.sender,
                    source_email_subject=email.subject,
                    created_at=now + timedelta(seconds=1),
                    updated_at=now + timedelta(seconds=1),
                )
            )
            db.add(
                EmailConversation(
                    owner_id=main.settings.owner_id,
                    root_recruiter_email_id=email.id,
                    external_thread_id="conversation-everywhere",
                    status="replied",
                    last_message_at=now + timedelta(seconds=2),
                    unread_reply_count=2,
                )
            )
            db.add(
                RecentRunSkippedItem(
                    owner_id=main.settings.owner_id,
                    run_source="gmail_sync",
                    run_key="gmail_sync:batch-everywhere",
                    source_type="gmail",
                    outcome="skipped",
                    reason_code="duplicate_existing_email",
                    reason_detail="Already imported",
                    candidate_email_id=email.id,
                    title_or_subject=email.subject,
                    sender=email.sender,
                    created_at=now + timedelta(seconds=3),
                )
            )
            db.add(
                RecentRun(
                    owner_id=main.settings.owner_id,
                    run_source="gmail_sync",
                    run_key="gmail_sync:batch-everywhere",
                    sync_batch_id="batch-everywhere",
                    status="ok",
                    detail="Imported email",
                    created_at=now + timedelta(seconds=4),
                    updated_at=now + timedelta(seconds=4),
                )
            )
            db.commit()
            email_id = email.id

        response = self.client.get("/search/email", params={"q": "everywhere@example.com"})

        self.assertEqual(response.status_code, 200, response.text)
        hits = response.json()["hits"]
        self.assertEqual({hit["section"] for hit in hits}, {"sent_items", "premium_numbers", "inbox", "recent_runs"})
        self.assertEqual(sum(hit["section"] == "recent_runs" for hit in hits), 2)
        self.assertTrue(all(hit["recruiter_email_id"] == email_id for hit in hits))
        details = [hit["detail"] for hit in hits]
        self.assertTrue(any("premium_number_lead_id" in detail for detail in details))
        self.assertTrue(any("conversation_id" in detail for detail in details))
        self.assertTrue(any("recent_run_skipped_item_id" in detail for detail in details))
        self.assertTrue(any("recent_run_id" in detail for detail in details))

    def test_unmatched_search_is_empty_and_short_query_is_rejected(self) -> None:
        empty = self.client.get("/search/email", params={"q": "missing@example.com"})
        short = self.client.get("/search/email", params={"q": "x"})
        whitespace = self.client.get("/search/email", params={"q": "  "})

        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json()["hits"], [])
        self.assertFalse(empty.json()["truncated"])
        self.assertEqual(short.status_code, 422, short.text)
        self.assertEqual(whitespace.status_code, 422, whitespace.text)

    def test_search_is_isolated_to_configured_owner(self) -> None:
        with Session(self.engine) as db:
            visible = self._add_email(db, sender="Owner Match <owner-match@example.com>", suffix="owner-visible")
            self._add_email(
                db,
                sender="Owner Match <owner-match@example.com>",
                owner_id="other-owner",
                suffix="owner-hidden",
            )
            db.commit()
            visible_id = visible.id

        response = self.client.get("/search/email", params={"q": "owner-match@example.com"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([hit["recruiter_email_id"] for hit in response.json()["hits"]], [visible_id])

    def test_recent_run_sender_match_can_return_a_skipped_item_without_a_candidate(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecentRunSkippedItem(
                    owner_id=main.settings.owner_id,
                    run_source="gmail_sync",
                    run_key="gmail_sync:no-candidate",
                    source_type="gmail",
                    outcome="skipped",
                    reason_code="intent_rejected",
                    reason_detail="Rejected before candidate creation",
                    candidate_email_id=None,
                    title_or_subject="Unrelated role",
                    sender="Skipped Recruiter <skipped-only@example.com>",
                )
            )
            db.commit()

        response = self.client.get("/search/email", params={"q": "skipped-only@example.com"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["hits"]), 1)
        self.assertEqual(response.json()["hits"][0]["section"], "recent_runs")
        self.assertIsNone(response.json()["hits"][0]["recruiter_email_id"])

    def test_premium_number_lead_matches_owner_name_and_formatted_phone(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, sender="Phone Recruiter <phone@example.com>", suffix="phone")
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=email.id,
                    phone_number_normalized="12145551212",
                    phone_number_display="(214) 555-1212",
                    owner_name="Dana Phoneowner",
                    company="Dial Inc",
                    designation="Recruiter",
                    purpose="Recruiter direct number",
                    confidence="high",
                    source_email_sender=email.sender,
                    source_email_subject=email.subject,
                )
            )
            db.commit()
            email_id = email.id

        by_owner_name = self.client.get("/search/email", params={"q": "Phoneowner"})
        by_formatted_phone = self.client.get("/search/email", params={"q": "(214) 555-1212"})

        self.assertEqual(by_owner_name.status_code, 200, by_owner_name.text)
        self.assertEqual(by_owner_name.json()["hits"][0]["recruiter_email_id"], email_id)
        self.assertEqual(by_formatted_phone.status_code, 200, by_formatted_phone.text)
        self.assertEqual(by_formatted_phone.json()["hits"][0]["recruiter_email_id"], email_id)

    def test_pending_number_review_matches_and_resolved_review_is_excluded(self) -> None:
        with Session(self.engine) as db:
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=999901,
                    normalized_phone_number="19805551234",
                    display_phone_number="(980) 555-1234",
                    owner_name="Review Owner",
                    company="Review Co",
                    email_sender="review@example.com",
                    email_subject="Pending review role",
                    state="pending",
                )
            )
            db.add(
                NumberReviewQueue(
                    owner_id=main.settings.owner_id,
                    source_email_id=999902,
                    normalized_phone_number="19805559999",
                    display_phone_number="(980) 555-9999",
                    owner_name="Resolved Owner",
                    company="Resolved Co",
                    email_sender="resolved@example.com",
                    email_subject="Already classified role",
                    state="classified",
                )
            )
            db.commit()

        pending = self.client.get("/search/email", params={"q": "Review Co"})
        resolved = self.client.get("/search/email", params={"q": "Resolved Co"})

        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(len(pending.json()["hits"]), 1)
        self.assertEqual(pending.json()["hits"][0]["section"], "premium_numbers")
        self.assertEqual(pending.json()["hits"][0]["recruiter_email_id"], 999901)
        self.assertEqual(resolved.json()["hits"], [])

    def test_recruiter_number_matches_by_phone_digits_and_hides_nvoids_placeholder(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="19807371481",
                    display_phone_number="(980) 737-1481",
                    recruiter_name="Nadia Number",
                    company="Numbers LLC",
                    recruiter_email="nadia@example.com",
                    first_detected_email_id=999903,
                )
            )
            db.add(
                RecruiterNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="nvoids-placeholder-numbers-llc",
                    display_phone_number="Unknown",
                    recruiter_name="Numbers LLC placeholder",
                    company="Numbers LLC",
                    first_detected_email_id=None,
                )
            )
            db.commit()

        by_phone = self.client.get("/search/email", params={"q": "(980) 737-1481"})
        by_company = self.client.get("/search/email", params={"q": "Numbers LLC"})

        self.assertEqual(by_phone.status_code, 200, by_phone.text)
        self.assertEqual(len(by_phone.json()["hits"]), 1)
        self.assertEqual(by_phone.json()["hits"][0]["recruiter_email_id"], 999903)
        # Both rows match "Numbers LLC" by company, but the nvoids placeholder must stay hidden.
        self.assertEqual(len(by_company.json()["hits"]), 1)
        self.assertEqual(by_company.json()["hits"][0]["sender"], "Nadia Number")

    def test_employer_number_matches_and_hides_invalid_placeholder(self) -> None:
        with Session(self.engine) as db:
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="12125551000",
                    display_phone_number="(212) 555-1000",
                    owner_name="Employer HR",
                    company="Acme Staffing",
                    source_email_id=999904,
                )
            )
            db.add(
                EmployerNumber(
                    owner_id=main.settings.owner_id,
                    normalized_phone_number="not-a-real-number",
                    display_phone_number="Acme Staffing front desk",
                    owner_name="Employer HR",
                    company="Acme Staffing",
                    source_email_id=None,
                )
            )
            db.commit()

        response = self.client.get("/search/email", params={"q": "Acme Staffing"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["hits"]), 1)
        self.assertEqual(response.json()["hits"][0]["recruiter_email_id"], 999904)

    def test_recruiter_opportunity_matches_by_job_title_and_client(self) -> None:
        with Session(self.engine) as db:
            db.add(
                RecruiterOpportunity(
                    owner_id=main.settings.owner_id,
                    recruiter_number_id=1,
                    gmail_message_id="opportunity-message-1",
                    source_email_id=999905,
                    email_subject="Senior QA Automation Engineer",
                    email_sender="opportunity@example.com",
                    job_title="Senior QA Automation Engineer",
                    client="Big Client Corp",
                    location="Remote",
                )
            )
            db.commit()

        response = self.client.get("/search/email", params={"q": "Big Client Corp"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["hits"]), 1)
        hit = response.json()["hits"][0]
        self.assertEqual(hit["section"], "premium_numbers")
        self.assertEqual(hit["recruiter_email_id"], 999905)
        self.assertEqual(hit["detail"]["job_title"], "Senior QA Automation Engineer")

    def test_results_rank_by_section_priority_before_recency(self) -> None:
        with Session(self.engine) as db:
            recent_email = self._add_email(
                db,
                sender="OrderPriority Recruiter <recent@example.com>",
                suffix="order-priority-recent",
                occurred_at=datetime.now(UTC),
            )
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=recent_email.id,
                    phone_number_normalized="12145557777",
                    phone_number_display="(214) 555-7777",
                    owner_name="OrderPriority Owner",
                    company="Older Dial Co",
                    designation="Recruiter",
                    purpose="Recruiter direct number",
                    confidence="high",
                    source_email_sender=recent_email.sender,
                    source_email_subject=recent_email.subject,
                    created_at=datetime.now(UTC) - timedelta(days=1),
                    updated_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            db.commit()

        response = self.client.get("/search/email", params={"q": "OrderPriority"})

        self.assertEqual(response.status_code, 200, response.text)
        hits = response.json()["hits"]
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0]["section"], "premium_numbers")
        self.assertEqual(hits[1]["section"], "needs_review")

    def test_current_section_param_overrides_default_priority(self) -> None:
        with Session(self.engine) as db:
            recent_email = self._add_email(
                db,
                sender="SectionOverride Recruiter <recent@example.com>",
                suffix="section-override-recent",
                occurred_at=datetime.now(UTC),
            )
            db.add(
                PremiumNumberLead(
                    owner_id=main.settings.owner_id,
                    recruiter_email_id=recent_email.id,
                    phone_number_normalized="12145558888",
                    phone_number_display="(214) 555-8888",
                    owner_name="SectionOverride Owner",
                    company="Older Dial Co",
                    designation="Recruiter",
                    purpose="Recruiter direct number",
                    confidence="high",
                    source_email_sender=recent_email.sender,
                    source_email_subject=recent_email.subject,
                    created_at=datetime.now(UTC) - timedelta(days=1),
                    updated_at=datetime.now(UTC) - timedelta(days=1),
                )
            )
            db.commit()

        default_response = self.client.get("/search/email", params={"q": "SectionOverride"})
        scoped_response = self.client.get(
            "/search/email", params={"q": "SectionOverride", "section": "needs_review"}
        )

        self.assertEqual(default_response.json()["hits"][0]["section"], "premium_numbers")
        self.assertEqual(scoped_response.status_code, 200, scoped_response.text)
        scoped_hits = scoped_response.json()["hits"]
        self.assertEqual(scoped_hits[0]["section"], "needs_review")
        self.assertEqual(scoped_hits[1]["section"], "premium_numbers")

    def test_result_cap_reports_truncation(self) -> None:
        with Session(self.engine) as db:
            for index in range(201):
                self._add_email(
                    db,
                    sender=f"Cap Match {index} <cap-match-{index}@example.com>",
                    suffix=f"cap-{index}",
                )
            db.commit()

        response = self.client.get("/search/email", params={"q": "cap-match-"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["hits"]), 200)
        self.assertTrue(response.json()["truncated"])


if __name__ == "__main__":
    unittest.main()
