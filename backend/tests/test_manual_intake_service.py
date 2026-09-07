"""Manual intake: pasted text in, a Needs Review card out.

Three things are being proved here, in the order they matter.

**The engine is called, not copied.** The expensive work - hard filter, blended
score, routing, draft - already exists as `prepare_candidate_for_queue`, the
same function the Gmail run and the Nvoids sync call. A future re-implementation
of it inside this service is the failure these tests exist to catch, so one of
them asserts the call itself rather than only the fields that come out of it.

**The two copies of the text stay separate.** The recruiter's address lives
below the sign-off, exactly where `strip_recruiter_footer` cuts. Read contacts
from parsed output and every paste loses its address; feed raw text to the JD
parser and the signature reads as job content. Both directions are pinned.

**Nothing about a contact is invented.** A missing address produces a Failed
Mapping card that says so, never a plausible-looking address.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai.resume_context_attribution import RESUME_CONTEXT_RULES_ONLY
from app.db import Base
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.services.manual_intake_service import (
    MISSING_EMAIL_SKIP_REASON,
    ManualIntakeDeps,
    ManualIntakeService,
    manual_intake_length_error,
    preview_duplicate,
)

OWNER = "default-owner"

WHATSAPP_REQUIREMENT = """Job Title:  Senior Full Stack Developer
Experience: 8+ Years
Client: TECH M
Job Type: Contract -C2C
Rate/Salary: $58/Hr on C2C
Job Location:  Plano, TX (onsite)
Relocation: Yes

Kindly don't re-submit your profile to the TECH M and for this same location and position.

Thanks & regards
T Mahesh royal
US It Recruiter
Fusion Global Technologies and Solutions
Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386
"""


def _fake_resume_selection(resume: ResumeAsset | None) -> SimpleNamespace:
    return SimpleNamespace(
        resume=resume,
        ai_score=0.91,
        ai_summary="Strong overlap on the stack.",
        ai_score_source="test",
        ats_score=88.0,
        ats_summary="ats",
        ats_score_source="test",
        ats_breakdown_json="{}",
        final_resume_score=0.91,
        selection_reason="only enabled resume",
        candidate_rankings_json="[]",
        picker_breakdown_json="{}",
        email_embedding_json=None,
        resume_embedding_json=None,
        semantic_diag=None,
    )


class ManualIntakeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        with self.SessionLocal() as db:
            # No location rule configured. These tests are about manual intake,
            # not about the location filter - and the rules parser reads
            # "Plano, TX (onsite)" as location "onsite", which is pre-existing
            # behaviour shared with the Gmail path, not something intake causes.
            # test_screening_still_blocks_a_bad_requirement covers the filter.
            db.add(UserSettings(owner_id=OWNER, accepted_locations=""))
            self.resume = ResumeAsset(
                owner_id=OWNER,
                file_path="/tmp/resume.pdf",
                file_name="resume.pdf",
                sha256="0" * 64,
                is_enabled=True,
                is_current=True,
            )
            db.add(self.resume)
            db.commit()
            self.resume_id = self.resume.id

        self.captured_premium: list[int] = []
        self.service = ManualIntakeService(
            ManualIntakeDeps(
                owner_id=OWNER,
                model_name="test-model",
                get_settings=lambda db: db.query(UserSettings).filter_by(owner_id=OWNER).one(),
                active_resume=lambda db: db.get(ResumeAsset, self.resume_id),
                enabled_resumes=lambda db: [db.get(ResumeAsset, self.resume_id)],
                evaluate_routing_policy=self._routing,
                apply_routing_decision=self._apply_routing,
                capture_premium_numbers=lambda db, email: self.captured_premium.append(email.id),
            )
        )
        # Only the two model-touching calls are faked. Extraction, screening,
        # role assignment, the hard filter and routing all run for real.
        self.patches = [
            patch.object(
                self.service.scoring_runtime,
                "select_best_resume_match",
                side_effect=lambda **kwargs: _fake_resume_selection(kwargs.get("fallback_resume")),
            ),
            patch(
                "app.services.manual_intake_service.generate_reply_with_ai_or_fallback",
                side_effect=lambda **kwargs: SimpleNamespace(
                    draft_text=kwargs["fallback_draft"],
                    source="rules_only",
                    ai_model=None,
                    ai_error=None,
                    resume_context_status=RESUME_CONTEXT_RULES_ONLY,
                ),
            ),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self) -> None:
        for active in reversed(self.patches):
            active.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    @staticmethod
    def _routing(db, sender, subject, body, snippet="", routing_confirmed=False, precomputed=None):
        return SimpleNamespace(
            to_email="mahesh@fusiongts.com",
            cc_email="employer@example.com",
            status="safe",
            confidence=0.95,
            reason="test",
            evidence=[],
            candidates=[],
            should_mark_failed=False,
            recommended_skip_reason=None,
        )

    @staticmethod
    def _apply_routing(email: RecruiterEmail, routing) -> None:
        email.recipient_email = email.recipient_email or routing.to_email
        email.cc_email = routing.cc_email
        email.routing_status = routing.status
        email.routing_confidence = routing.confidence
        email.routing_reason = routing.reason

    def _ingest(self, text: str = WHATSAPP_REQUIREMENT):
        with self.SessionLocal() as db:
            return self.service.ingest(db, text=text)

    def _row(self, email_id: int) -> RecruiterEmail:
        with self.SessionLocal() as db:
            return db.get(RecruiterEmail, email_id)

    # --- A. source labeling ------------------------------------------------

    def test_a_pasted_requirement_is_labelled_manual(self) -> None:
        row = self._row(self._ingest().email_id)
        self.assertEqual(row.source, "manual")

    def test_the_gmail_fields_are_empty_not_faked(self) -> None:
        row = self._row(self._ingest().email_id)
        self.assertIsNone(row.external_message_id)
        self.assertIsNone(row.external_thread_id)
        self.assertIsNone(row.gmail_received_at)

    def test_the_lineage_record_says_manual(self) -> None:
        from app.models import CandidateRecord

        row = self._row(self._ingest().email_id)
        with self.SessionLocal() as db:
            record = db.get(CandidateRecord, row.record_id)
        self.assertEqual(record.origin_type, "manual")

    # --- C. the two copies of the text -------------------------------------

    def test_the_address_beneath_the_sign_off_reaches_the_card(self) -> None:
        """The footer test. Reading contacts from parsed output fails only here."""
        result = self._ingest()
        row = self._row(result.email_id)
        self.assertEqual(row.recipient_email, "mahesh@fusiongts.com")
        self.assertEqual(row.state, "needs_review")

    def test_the_stored_body_is_the_paste_verbatim(self) -> None:
        row = self._row(self._ingest().email_id)
        self.assertEqual(row.body, WHATSAPP_REQUIREMENT)
        self.assertIn("Thanks & regards", row.body)
        self.assertIn("mahesh@fusiongts.com", row.body)

    def test_footer_text_is_not_treated_as_job_content(self) -> None:
        """The mirror of the footer test, and the reason the stripped copy exists.

        A technology named only in the signature must not become a required
        skill, or the recruiter's own strapline starts driving resume selection.
        """
        text = WHATSAPP_REQUIREMENT.replace(
            "Fusion Global Technologies and Solutions",
            "Fusion Global Technologies and Solutions - Salesforce and Workday partners",
        )
        row = self._row(self._ingest(text).email_id)
        self.assertNotIn("salesforce", (row.skills_text or "").lower())
        self.assertNotIn("salesforce", (row.skills_json or "").lower())
        self.assertNotIn("workday", (row.skills_text or "").lower())
        # ...while the body still holds every word of it.
        self.assertIn("Salesforce and Workday partners", row.body)

    def test_the_sender_carries_the_extracted_identity(self) -> None:
        row = self._row(self._ingest().email_id)
        self.assertEqual(row.sender, "T Mahesh royal <mahesh@fusiongts.com>")

    # --- C. no recruiter email --------------------------------------------

    def test_a_requirement_with_no_address_is_still_fully_processed(self) -> None:
        text = WHATSAPP_REQUIREMENT.replace(
            "Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386",
            "Contact: +1 (210) 485-6386",
        )
        row = self._row(self._ingest(text).email_id)
        # The work is kept: parsed, scored, resume picked, draft written.
        self.assertTrue(row.role)
        self.assertEqual(row.resume_asset_id, self.resume_id)
        self.assertTrue(row.draft_reply)

    def test_a_requirement_with_no_address_goes_to_failed_mapping(self) -> None:
        text = WHATSAPP_REQUIREMENT.replace(
            "Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386",
            "Contact: +1 (210) 485-6386",
        )
        result = self._ingest(text)
        row = self._row(result.email_id)
        self.assertEqual(row.state, "failed")
        self.assertFalse(row.routing_confirmed)
        self.assertEqual(row.routing_status, "ambiguous")
        self.assertEqual(row.skip_reason, MISSING_EMAIL_SKIP_REASON)
        self.assertIn("Add one to enable outreach", row.last_error)
        self.assertIn("Failed Mapping", result.detail)

    def test_no_address_is_ever_invented(self) -> None:
        """The test that must never be relaxed.

        A guessed address sends a real person's resume - and, with the document
        locker, their passport - to a stranger.
        """
        text = WHATSAPP_REQUIREMENT.replace(
            "Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386",
            "Contact: +1 (210) 485-6386",
        )
        row = self._row(self._ingest(text).email_id)
        self.assertFalse(row.recipient_email)
        # Not constructed from the company name that is right there in the text.
        self.assertNotIn("fusiongts", (row.recipient_email or ""))
        self.assertNotIn("fusion", (row.sender or "").lower().split("<")[-1])

    # --- E. pipeline reuse -------------------------------------------------

    def test_extraction_fills_the_requirement_fields(self) -> None:
        """The same parser the Gmail path uses, run over the stripped copy.

        Asserted on role and the stored parser payload rather than on rate: the
        rules parser returns "not_specified" for `$58/Hr` in every phrasing, with
        or without the AI extractor, and that is shared behaviour with Gmail and
        Nvoids rather than anything intake introduces.
        """
        row = self._row(self._ingest().email_id)
        self.assertIn("full stack", (row.role or "").lower())
        self.assertTrue(row.parser_details_json)
        self.assertIn("Plano, TX", row.parser_details_json)

    def test_the_resume_is_selected_and_a_draft_is_written(self) -> None:
        row = self._row(self._ingest().email_id)
        self.assertEqual(row.resume_asset_id, self.resume_id)
        self.assertEqual(row.resume_file_name, "resume.pdf")
        self.assertTrue(row.draft_reply.strip())
        self.assertAlmostEqual(row.ai_score, 0.91, places=3)

    def test_screening_still_blocks_a_bad_requirement(self) -> None:
        """The hard filter runs on a paste exactly as it does on a Gmail message."""
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter_by(owner_id=OWNER).one()
            row.accepted_locations = "Remote"
            db.commit()

        text = WHATSAPP_REQUIREMENT.replace("Plano, TX (onsite)", "New York, NY")
        card = self._row(self._ingest(text).email_id)
        self.assertEqual(card.state, "auto_rejected")
        self.assertIn("location", (card.auto_reject_reason or "").lower())
        # Still stored, still attributed, still auditable.
        self.assertEqual(card.source, "manual")
        self.assertEqual(card.recipient_email, "mahesh@fusiongts.com")

    def test_premium_contact_capture_runs_after_the_row_is_persisted(self) -> None:
        """It reads a persisted RecruiterEmail, so ordering is load-bearing."""
        result = self._ingest()
        self.assertEqual(self.captured_premium, [result.email_id])

    def test_the_shared_engine_is_called_not_reimplemented(self) -> None:
        """Guards the architecture decision, not just its output.

        Asserting only on the resulting fields would pass just as well against a
        fourth hand-rolled copy of the hard filter, the score and the draft.
        """
        with patch(
            "app.services.manual_intake_service.prepare_candidate_for_queue",
            wraps=__import__(
                "app.services.manual_intake_service", fromlist=["prepare_candidate_for_queue"]
            ).prepare_candidate_for_queue,
        ) as engine:
            self._ingest()
        engine.assert_called_once()
        request = engine.call_args.args[0]
        # The engine sees the stripped copy; only the stored row keeps the footer.
        self.assertNotIn("mahesh@fusiongts.com", request.body)
        self.assertIsNone(request.external_thread_id)

    def test_gmail_only_helpers_are_never_called(self) -> None:
        with patch("app.parsing.document_extraction.prepare_gmail_parse_body") as gmail_prep:
            self._ingest()
        gmail_prep.assert_not_called()

    # --- duplicates ---------------------------------------------------------

    def test_pasting_the_same_requirement_twice_is_detected(self) -> None:
        first = self._ingest()
        with self.SessionLocal() as db:
            duplicate = self.service.preview(db, text=WHATSAPP_REQUIREMENT)
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate.id, first.email_id)

    def test_the_module_level_duplicate_preview_matches_the_service(self) -> None:
        first = self._ingest()
        with self.SessionLocal() as db:
            duplicate = preview_duplicate(db, owner_id=OWNER, text=WHATSAPP_REQUIREMENT)
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate.id, first.email_id)

    def test_the_shared_length_check_uses_the_configured_boundary(self) -> None:
        with patch("app.services.manual_intake_service.settings.manual_intake_max_chars", 5):
            self.assertIsNone(manual_intake_length_error("12345"))
            self.assertIn("6", manual_intake_length_error("123456"))
            self.assertEqual(manual_intake_length_error("   "), "Pasted requirement is empty")

    def test_a_duplicate_is_never_blocked(self) -> None:
        """A recruiter re-sending an updated requirement is a normal event."""
        first = self._ingest()
        second = self._ingest()
        self.assertNotEqual(first.email_id, second.email_id)
        with self.SessionLocal() as db:
            self.assertEqual(db.query(RecruiterEmail).count(), 2)

    def test_a_differently_worded_posting_is_not_a_duplicate(self) -> None:
        """Similarity is not attempted, and this is the guard that keeps it out.

        Same role, same client, different recruiter and wording: nothing in the
        text can say whether that is one requirement or two submissions.
        """
        self._ingest()
        other = WHATSAPP_REQUIREMENT.replace("T Mahesh royal", "Pat Recruiter").replace(
            "mahesh@fusiongts.com", "pat@othervendor.com"
        )
        with self.SessionLocal() as db:
            self.assertIsNone(self.service.preview(db, text=other))

    def test_whitespace_and_case_differences_still_collide(self) -> None:
        self._ingest()
        noisy = WHATSAPP_REQUIREMENT.replace("Job Title:", "job title:").replace("\n", "\n ")
        with self.SessionLocal() as db:
            self.assertIsNotNone(self.service.preview(db, text=noisy))

    def test_a_gmail_row_never_triggers_a_manual_duplicate_warning(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecruiterEmail(
                    owner_id=OWNER,
                    sender="r@example.com",
                    subject="Senior Full Stack Developer",
                    body=WHATSAPP_REQUIREMENT,
                    source="gmail",
                )
            )
            db.commit()
        with self.SessionLocal() as db:
            self.assertIsNone(self.service.preview(db, text=WHATSAPP_REQUIREMENT))

    def test_preview_of_empty_text_is_not_a_duplicate(self) -> None:
        with self.SessionLocal() as db:
            self.assertIsNone(self.service.preview(db, text="   \n "))

    def test_ingesting_empty_text_is_refused(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(ValueError):
                self.service.ingest(db, text="   ")


if __name__ == "__main__":
    unittest.main()
