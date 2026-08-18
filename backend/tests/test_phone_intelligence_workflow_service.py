import inspect
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import NumberReviewQueue, PremiumNumberContact, PremiumNumberLead, RecruiterEmail, RecruiterOpportunity
from app.premium_numbers.extraction import ExtractedContactGroup
from app.services.phone_intelligence_workflow_service import (
    PhoneIntelligenceWorkflowService,
    PhoneWorkflowSourceContext,
    _context_from_external_opportunity,
    _context_from_recruiter_email,
)


def _lead(
    *,
    role: str = "unknown",
    owner_name: str = "Unknown",
    contact_email: str = "",
    company: str = "Unknown",
    relevance_score: int = 0,
    relevant: bool = False,
    reason: str = "insufficient_signals",
) -> ExtractedContactGroup:
    return ExtractedContactGroup(
        phone_number_display="(214) 555-1212",
        phone_number_normalized="12145551212",
        owner_name=owner_name,
        contact_email=contact_email,
        company=company,
        designation="Recruiter" if role == "recruiter" else "Manager",
        purpose="Direct contact",
        confidence="high",
        contact_type="recruiter_direct" if role == "recruiter" else "unknown",
        recruiter_relevance_score=relevance_score,
        is_recruiter_relevant=relevant,
        relevance_reason=reason,
        source_fragment="Call this contact",
        role=role,
        extraction_source="ai",
    )


class PhoneIntelligenceWorkflowServiceTests(unittest.TestCase):
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
    def _email(db: Session, message_id: str) -> RecruiterEmail:
        email = RecruiterEmail(
            owner_id="default-owner",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            role="Python Developer",
            location="Dallas",
            salary_text="",
            skills_text="Python, SQL",
            score=80,
            decision="Qualified",
            state="needs_review",
            draft_reply="Thanks",
            source="gmail",
            external_message_id=message_id,
            external_thread_id=f"thread-{message_id}",
            gmail_received_at=datetime.now(UTC),
            end_client="End Client Co",
            implementation_partner="Implementation Co",
            domain="Healthcare",
            resume_file_name="python-resume.pdf",
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    @staticmethod
    def _external(db: Session) -> ExternalOpportunity:
        source = ExternalFeedSource(
            owner_id="default-owner",
            source_type="nvoids",
            base_url="https://nvoids.example",
            enabled=True,
        )
        db.add(source)
        db.flush()
        item = ExternalOpportunity(
            owner_id="default-owner",
            feed_source_id=source.id,
            source_type="nvoids",
            external_post_id="post-1",
            source_url="https://nvoids.example/post-1",
            recruiter_email="row2@agency.example",
            recruiter_phone="(214) 555-1212",
            recruiter_name="Regex Guess",
            company="Client Co",
            role="Python Developer",
            location="Dallas",
            skills_text="Python",
            raw_body="Call (214) 555-1212",
            raw_html="",
            dedupe_hash="hash-1",
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item

    def test_version_append_does_not_overwrite_active_fields(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Legacy Name",
                company="Legacy Co",
                designation="Legacy Recruiter",
                recruiter_email="legacy@example.com",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            email = self._email(db, "gmail-1")
            current = _lead(
                role="recruiter",
                owner_name="New Name",
                contact_email="new@agency.example",
                company="New Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[current],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            versions = db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).all()
            self.assertEqual(len(versions), 2)
            active = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertIsNotNone(active)
            self.assertEqual(active.extraction_source, "legacy_snapshot")
            self.assertEqual(contact.recruiter_name, "Legacy Name")
            self.assertEqual(db.query(RecruiterOpportunity).count(), 1)

    def test_legacy_snapshot_created_on_first_new_version(self) -> None:
        """temp122.md `## 7. Workstream D` cutover behavior: the first new-model
        version linked onto an already-existing legacy contact (zero linked
        `premium_number_leads` rows for that role) synthesizes a
        `legacy_snapshot` version and sets it active *before* the new
        incoming version is appended as a second, non-active row.
        """
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Legacy Name",
                company="Legacy Co",
                designation="Legacy Recruiter",
                recruiter_email="legacy@example.com",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            self.assertIsNone(contact.active_recruiter_lead_id)
            self.assertEqual(
                db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).count(),
                0,
            )

            email = self._email(db, "gmail-legacy-snapshot")
            current = _lead(
                role="recruiter",
                owner_name="New Name",
                contact_email="new@agency.example",
                company="New Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[current],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            versions = db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact.id).all()
            self.assertEqual(len(versions), 2)
            self.assertIn("legacy_snapshot", {version.extraction_source for version in versions})
            active = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
            self.assertIsNotNone(active)
            self.assertEqual(active.extraction_source, "legacy_snapshot")

    def test_shared_switchboard_number_gets_both_role_flags(self) -> None:
        with Session(self.engine) as db:
            contact = PremiumNumberContact(
                owner_id="default-owner",
                normalized_phone_number="12145551212",
                display_phone_number="(214) 555-1212",
                is_recruiter=True,
                recruiter_name="Recruiter",
            )
            db.add(contact)
            db.commit()
            db.refresh(contact)
            email = self._email(db, "gmail-employer")
            employer = _lead(
                role="employer",
                owner_name="Hiring Desk",
                contact_email="desk@client.example",
                company="Client Co",
                reason="employer_domain",
            )
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[employer],
            ):
                PhoneIntelligenceWorkflowService().capture_premium_numbers(db, email)

            db.refresh(contact)
            self.assertTrue(contact.is_recruiter)
            self.assertTrue(contact.is_employer)
            self.assertEqual(contact.owner_name, "Hiring Desk")
            self.assertIsNotNone(contact.active_employer_lead_id)

    def test_nvoids_uncertain_lead_lands_in_needs_review_with_row2_email(self) -> None:
        with Session(self.engine) as db:
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                result = PhoneIntelligenceWorkflowService().capture_premium_numbers_for_nvoids(
                    db,
                    item,
                    item.raw_body,
                )

            self.assertEqual(result.review_created, 1)
            review = db.query(NumberReviewQueue).one()
            self.assertIsNone(review.source_email_id)
            self.assertEqual(review.source_external_opportunity_id, item.id)
            self.assertEqual(review.contact_email, "row2@agency.example")
            version = db.get(PremiumNumberLead, review.source_lead_id)
            self.assertEqual(version.contact_email, "row2@agency.example")
            self.assertEqual(db.query(PremiumNumberContact).count(), 0)

    def test_idempotency_distinguishes_gmail_and_nvoids_rows(self) -> None:
        with Session(self.engine) as db:
            email = self._email(db, "gmail-distinct")
            item = self._external(db)
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                service = PhoneIntelligenceWorkflowService()
                service.capture_premium_numbers(db, email)
                service.capture_premium_numbers_for_nvoids(db, item, item.raw_body)
                service.capture_premium_numbers_for_nvoids(db, item, item.raw_body)

            self.assertEqual(db.query(PremiumNumberLead).count(), 2)
            self.assertEqual(db.query(NumberReviewQueue).count(), 2)

    def test_build_snapshot_uses_recruiter_email_entity_fields(self) -> None:
        context = PhoneWorkflowSourceContext(
            owner_id="default-owner",
            source="gmail",
            subject="Python Developer",
            body="Client: Wrong Regex Client\nLocation: Dallas",
            sender="jobs@agency.example",
            open_url="https://mail.example/message",
            dedupe_key="gmail-entity-fields",
            received_at=datetime.now(UTC),
            recruiter_email_row_id=1,
            external_opportunity_row_id=None,
            end_client="AI End Client",
            implementation_partner="AI Partner",
            domain="Healthcare",
            skills_text="Python, SQL, Kubernetes",
            resume_file_name="python-resume.pdf",
        )
        snapshot = PhoneIntelligenceWorkflowService()._build_snapshot(7, context, _lead())
        self.assertEqual(snapshot.end_client, "AI End Client")
        self.assertEqual(snapshot.implementation_partner, "AI Partner")
        self.assertEqual(snapshot.domain, "Healthcare")
        self.assertEqual(snapshot.extracted_skills, "Python, SQL, Kubernetes")
        self.assertEqual(snapshot.resume_file_name, "python-resume.pdf")

    def test_context_from_recruiter_email_matches_existing_fields(self) -> None:
        """temp121.md `### 25.10`: the gmail adapter must map every source
        field from a `RecruiterEmail` onto `PhoneWorkflowSourceContext`,
        including the mutually-exclusive row-id invariant.
        """
        email = RecruiterEmail(
            id=90001,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
            external_message_id="adapter-gmail-msg-1",
            external_thread_id="adapter-gmail-thread-1",
            gmail_received_at=datetime(2026, 1, 5, tzinfo=UTC),
            end_client="End Client Co",
            implementation_partner="Implementation Co",
            domain="Healthcare",
            skills_text="Python, SQL",
            resume_file_name="python-resume.pdf",
        )

        context = _context_from_recruiter_email(email, source="gmail")

        self.assertEqual(context.owner_id, email.owner_id)
        self.assertEqual(context.source, "gmail")
        self.assertEqual(context.subject, email.subject)
        self.assertEqual(context.body, email.body)
        self.assertEqual(context.sender, email.sender)
        self.assertEqual(context.open_url, email.gmail_message_url)
        self.assertEqual(context.dedupe_key, email.external_message_id)
        self.assertEqual(context.received_at, email.gmail_received_at)
        self.assertEqual(context.recruiter_email_row_id, email.id)
        self.assertIsNone(context.external_opportunity_row_id)
        self.assertEqual(context.end_client, email.end_client)
        self.assertEqual(context.implementation_partner, email.implementation_partner)
        self.assertEqual(context.domain, email.domain)
        self.assertEqual(context.skills_text, email.skills_text)
        self.assertEqual(context.resume_file_name, email.resume_file_name)

        # dedupe_key falls back to "manual-{id}" when there's no external_message_id.
        manual_email = RecruiterEmail(
            id=90002,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
        )
        manual_context = _context_from_recruiter_email(manual_email)
        self.assertEqual(manual_context.dedupe_key, "manual-90002")
        self.assertEqual(manual_context.recruiter_email_row_id, 90002)
        self.assertIsNone(manual_context.external_opportunity_row_id)

        # nullable text fields fall back to "" rather than surfacing None.
        empty_fields_email = RecruiterEmail(
            id=90003,
            owner_id="owner-adapter-gmail",
            sender="jobs@agency.example",
            subject="Python Developer",
            body="Call (214) 555-1212",
            source="gmail",
            end_client=None,
            implementation_partner=None,
            domain=None,
            skills_text=None,
            resume_file_name=None,
        )
        empty_context = _context_from_recruiter_email(empty_fields_email)
        self.assertEqual(empty_context.end_client, "")
        self.assertEqual(empty_context.implementation_partner, "")
        self.assertEqual(empty_context.domain, "")
        self.assertEqual(empty_context.skills_text, "")
        self.assertEqual(empty_context.resume_file_name, "")

    def test_context_from_external_opportunity_matches_existing_fields(self) -> None:
        """temp121.md `### 25.10`: the nvoids adapter must map every source
        field from an `ExternalOpportunity` onto `PhoneWorkflowSourceContext`,
        including the mutually-exclusive row-id invariant.
        """
        item = ExternalOpportunity(
            id=90101,
            owner_id="owner-adapter-nvoids",
            feed_source_id=1,
            source_type="nvoids",
            external_post_id="post-adapter-1",
            source_url="https://nvoids.example/post-adapter-1",
            posted_at=datetime(2026, 1, 6, tzinfo=UTC),
            recruiter_email="row2@agency.example",
            recruiter_phone="(214) 555-1212",
            recruiter_name="Regex Guess",
            company="Client Co",
            role="Python Developer",
            location="Dallas",
            skills_text="Python",
            raw_body="Call (214) 555-1212",
            raw_html="",
            dedupe_hash="hash-adapter-1",
        )
        jd_body = "Full JD body text with (214) 555-1212"

        context = _context_from_external_opportunity(item, jd_body)

        self.assertEqual(context.owner_id, item.owner_id)
        self.assertEqual(context.source, "nvoids")
        self.assertEqual(context.subject, item.role)
        self.assertEqual(context.body, jd_body)
        self.assertEqual(context.sender, item.recruiter_email)
        self.assertEqual(context.open_url, item.source_url)
        self.assertEqual(context.dedupe_key, f"nvoids:{item.external_post_id}")
        self.assertEqual(context.received_at, item.posted_at)
        self.assertIsNone(context.recruiter_email_row_id)
        self.assertEqual(context.external_opportunity_row_id, item.id)
        self.assertEqual(context.end_client, item.company)
        self.assertEqual(context.skills_text, item.skills_text)
        # No equivalent source for these fields on the nvoids side (§23.1) —
        # the adapter leaves them at the dataclass defaults.
        self.assertEqual(context.implementation_partner, "")
        self.assertEqual(context.domain, "")
        self.assertEqual(context.resume_file_name, "")

        # falls back to raw_body when jd_body is falsy.
        fallback_context = _context_from_external_opportunity(item, "")
        self.assertEqual(fallback_context.body, item.raw_body)

        # nullable/blank text fields fall back to "" rather than surfacing None.
        empty_item = ExternalOpportunity(
            id=90102,
            owner_id="owner-adapter-nvoids",
            feed_source_id=1,
            source_type="nvoids",
            external_post_id="post-adapter-2",
            recruiter_email="",
            company="",
            role="",
            skills_text="",
            dedupe_hash="hash-adapter-2",
        )
        empty_context = _context_from_external_opportunity(empty_item, "")
        self.assertEqual(empty_context.subject, "")
        self.assertEqual(empty_context.sender, "")
        self.assertEqual(empty_context.end_client, "")
        self.assertEqual(empty_context.skills_text, "")
        self.assertEqual(empty_context.body, "")

    def test_gmail_public_methods_unchanged(self) -> None:
        """temp121.md `### 25.10`/`### 25.12`/`### 25.13`: "the single most
        load-bearing test in this entire plan" and "the primary regression
        gate for the highest-risk part of this change." Asserts (a) the four
        public wrapper methods (`process_email`, `extract_only`,
        `classify_only`, `capture_premium_numbers`) still expose the exact
        call conventions every existing caller depends on (§25.5: 4 sites in
        `run_orchestrator.py`, 3 in `main.py`, 1 each in
        `candidate_runtime_service.py` and `premium_numbers/service.py`), and
        (b) each one still produces correct behavior end to end through the
        refactored `PhoneWorkflowSourceContext`/`_run()` internals.
        """
        service_cls = PhoneIntelligenceWorkflowService

        def _param_shape(method: object) -> list[tuple[str, object, bool]]:
            return [
                (name, param.kind, param.default is inspect.Parameter.empty)
                for name, param in inspect.signature(method).parameters.items()
            ]

        # (a) call-convention regression gate.
        self.assertEqual(
            _param_shape(service_cls.process_email),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.extract_only),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.classify_only),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("source", inspect.Parameter.KEYWORD_ONLY, True),
            ],
        )
        self.assertEqual(
            _param_shape(service_cls.capture_premium_numbers),
            [
                ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("db", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
                ("email", inspect.Parameter.POSITIONAL_OR_KEYWORD, True),
            ],
        )

        # (b) behavioral regression gate, run against a real database.
        with Session(self.engine) as db:
            service = service_cls()
            recruiter_lead = _lead(
                role="recruiter",
                owner_name="Regression Recruiter",
                contact_email="recruiter@agency.example",
                company="Agency Co",
                relevance_score=95,
                relevant=True,
                reason="external_domain",
            )

            # capture_premium_numbers delegates to process_email(source="gmail")
            # (phone_intelligence_workflow_service.py:199) — prove the
            # delegation is intact: equivalent input through either entry
            # point produces identical outcomes.
            email_a = self._email(db, "gmail-unchanged-a")
            email_b = self._email(db, "gmail-unchanged-b")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ):
                via_capture = service.capture_premium_numbers(db, email_a)
                via_process = service.process_email(db, email_b, source="gmail")

            self.assertEqual(via_capture.source, "gmail")
            self.assertEqual(via_process.source, "gmail")
            self.assertEqual(
                (
                    via_capture.stored_count,
                    via_capture.review_created,
                    via_capture.recruiter_matches,
                    via_capture.opportunity_created,
                ),
                (
                    via_process.stored_count,
                    via_process.review_created,
                    via_process.recruiter_matches,
                    via_process.opportunity_created,
                ),
            )
            self.assertEqual(via_capture.recruiter_matches, 1)
            self.assertEqual(db.query(PremiumNumberContact).count(), 1)

            # extract_only: stores the lead version but performs no
            # classification/routing (include_intelligence=False); returns
            # stored_count as a plain int (premium_numbers/service.py:11's
            # calling convention).
            email_c = self._email(db, "gmail-unchanged-extract")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ):
                stored = service.extract_only(db, email_c, source="legacy_extract")
            self.assertIsInstance(stored, int)
            self.assertEqual(stored, 1)
            self.assertEqual(db.query(NumberReviewQueue).count(), 0)
            version = (
                db.query(PremiumNumberLead)
                .filter(PremiumNumberLead.recruiter_email_id == email_c.id)
                .one()
            )
            self.assertEqual(version.contact_email, "recruiter@agency.example")

            # classify_only: performs classification/routing but does not
            # upsert a premium_number_leads version
            # (include_premium_lead_upsert=False).
            email_d = self._email(db, "gmail-unchanged-classify")
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[_lead()],
            ):
                result = service.classify_only(db, email_d, source="candidate_runtime")
            self.assertEqual(result.review_created, 1)
            self.assertEqual(result.stored_count, 0)
            review = (
                db.query(NumberReviewQueue)
                .filter(NumberReviewQueue.source_email_id == email_d.id)
                .one()
            )
            self.assertIsNone(review.source_lead_id)

    def test_run_produces_equivalent_result_for_equivalent_context(self) -> None:
        """temp121.md `### 25.10`/`### 25.1`: proves `_run()`'s shared
        classification/routing logic is genuinely source-agnostic — not just
        source-agnostic in signature — by feeding it two independently
        adapter-built contexts carrying equivalent subject/body/sender data
        and asserting equivalent classification/routing outcomes.
        """
        with Session(self.engine) as db:
            gmail_email = RecruiterEmail(
                owner_id="owner-run-equiv-gmail",
                sender="jobs@agency.example",
                subject="Python Developer",
                body="Call (214) 555-1212",
                role="Python Developer",
                location="Dallas",
                salary_text="",
                skills_text="Python, SQL",
                score=80,
                decision="Qualified",
                state="needs_review",
                draft_reply="",
                source="gmail",
                external_message_id="run-equiv-gmail-1",
                external_thread_id="run-equiv-thread-1",
                gmail_received_at=datetime.now(UTC),
            )
            db.add(gmail_email)
            db.commit()
            db.refresh(gmail_email)

            feed_source = ExternalFeedSource(
                owner_id="owner-run-equiv-nvoids",
                source_type="nvoids",
                base_url="https://nvoids.example",
                enabled=True,
            )
            db.add(feed_source)
            db.flush()
            nvoids_item = ExternalOpportunity(
                owner_id="owner-run-equiv-nvoids",
                feed_source_id=feed_source.id,
                source_type="nvoids",
                external_post_id="run-equiv-post-1",
                source_url="https://nvoids.example/run-equiv-post-1",
                recruiter_email="jobs@agency.example",
                role="Python Developer",
                company="Client Co",
                skills_text="Python",
                raw_body="Call (214) 555-1212",
                raw_html="",
                dedupe_hash="run-equiv-hash-1",
            )
            db.add(nvoids_item)
            db.commit()
            db.refresh(nvoids_item)

            gmail_context = _context_from_recruiter_email(gmail_email, source="gmail")
            nvoids_context = _context_from_external_opportunity(nvoids_item, "Call (214) 555-1212")

            # The two contexts must actually carry equivalent input data —
            # otherwise an equal result wouldn't prove source-agnosticism.
            self.assertEqual(gmail_context.subject, nvoids_context.subject)
            self.assertEqual(gmail_context.body, nvoids_context.body)
            self.assertEqual(gmail_context.sender, nvoids_context.sender)

            recruiter_lead = _lead(
                role="recruiter",
                owner_name="Equivalence Recruiter",
                contact_email="jobs@agency.example",
                company="Agency Co",
                relevance_score=90,
                relevant=True,
                reason="external_domain",
            )
            service = PhoneIntelligenceWorkflowService()
            with patch(
                "app.services.phone_intelligence_workflow_service.extract_phone_leads",
                return_value=[recruiter_lead],
            ) as mocked_extract:
                gmail_result = service._run(
                    db,
                    gmail_context,
                    include_premium_lead_upsert=True,
                    include_intelligence=True,
                )
                nvoids_result = service._run(
                    db,
                    nvoids_context,
                    include_premium_lead_upsert=True,
                    include_intelligence=True,
                )

            # The shared extraction call itself received equivalent
            # sender/subject/body/employer_domains regardless of source.
            self.assertEqual(
                mocked_extract.call_args_list[0],
                mocked_extract.call_args_list[1],
            )

            def _outcome(result: object) -> tuple:
                return (
                    result.processed_numbers,
                    result.stored_count,
                    result.review_created,
                    result.review_existing,
                    result.recruiter_matches,
                    result.employer_matches,
                    result.opportunity_created,
                    result.opportunity_existing,
                    result.skipped_by_domain_guard,
                )

            self.assertEqual(_outcome(gmail_result), _outcome(nvoids_result))
            self.assertEqual(gmail_result.recruiter_matches, 1)


if __name__ == "__main__":
    unittest.main()
