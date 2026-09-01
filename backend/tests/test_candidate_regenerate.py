import json
import os
import tempfile
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity
from app.models import CandidateRecord, OpportunityLifecycleEvent, OpportunityLineage, RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision
from app.services import orchestration_service as orchestration_module
from app.services.role_manifest_service import MaterializedRequirement, RoleManifest, RoleManifestResult


class CandidateRegenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        main.orchestration_service = None
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

        with Session(self.engine) as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    enabled=True,
                    gmail_query="java is:unread",
                    default_gmail_query="java is:unread",
                    default_date_mode="off",
                    qualification_threshold=0.6,
                    feature_ai_enabled=True,
                    feature_ai_extractor_enabled=True,
                    feature_semantic_enabled=True,
                    fallback_draft_template="Hi",
                    signature_name="Tester",
                    signature_phone="+1",
                    signature_email="tester@example.com",
                    policy_json="",
                )
            )
            db.commit()

    def tearDown(self) -> None:
        main.orchestration_service = None
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _add_resume(self, db: Session, *, file_name: str = "resume.pdf", current: bool = True, version: int = 1) -> ResumeAsset:
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 fake")
        resume = ResumeAsset(
            owner_id=main.settings.owner_id,
            file_path=path,
            file_name=file_name,
            mime_type="application/pdf",
            sha256=f"sha-{file_name}",
            version=version,
            skills_text="java, spring",
            is_enabled=True,
            is_current=current,
            semantic_embedding=None,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    def _resume_like(self, resume: ResumeAsset) -> SimpleNamespace:
        return SimpleNamespace(
            id=resume.id,
            file_path=resume.file_path,
            file_name=resume.file_name,
            semantic_embedding=None,
            skills_text="java, spring",
        )

    def _add_feed_source(self, db: Session) -> ExternalFeedSource:
        source = ExternalFeedSource(owner_id=main.settings.owner_id, source_type="nvoids", base_url="https://www.nvoids.com/")
        db.add(source)
        db.commit()
        db.refresh(source)
        return source

    def _add_external_opportunity(self, db: Session, *, feed_source_id: int, external_post_id: str) -> ExternalOpportunity:
        row = ExternalOpportunity(
            owner_id=main.settings.owner_id,
            feed_source_id=feed_source_id,
            source_type="nvoids",
            external_post_id=external_post_id,
            source_url="https://www.nvoids.com/job_details.jsp?id=123",
            recruiter_email="nvoids@example.com",
            recruiter_name="Nvoids Recruiter",
            company="Nvoids Co",
            role="Senior Java Developer",
            location="Remote, USA",
            work_mode="Remote",
            visa_hints="Mentioned",
            rate="$70/hr",
            skills_text="Java, Spring Boot",
            raw_body="Stored raw nvoids body",
            raw_html="<html>nvoids raw html</html>",
            dedupe_hash=f"hash-{external_post_id}",
            parse_confidence=0.9,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _add_email(
        self,
        db: Session,
        *,
        source: str = "gmail",
        state: str = "needs_review",
        routing_confirmed: bool = False,
        external_message_id: str | None = None,
        body: str | None = None,
    ) -> RecruiterEmail:
        now = datetime.now(UTC)
        email = RecruiterEmail(
            owner_id=main.settings.owner_id,
            sender="Recruiter <r@example.com>" if source == "gmail" else "Recruiter <nvoids@example.com>",
            subject="Java role",
            body=body if body is not None else "Body with recruiter@example.com and manager@example.com",
            role="Old Role",
            location="Old Location",
            salary_text="$60/hr",
            skills_text="Old Skills",
            skills_json=json.dumps({"approved": ["Old Skills"]}),
            score=45,
            decision="Qualified",
            state=state,
            decision_reason="Before regenerate",
            hard_filter_result=None,
            auto_reject_reason=None,
            ai_score=0.45,
            ai_score_source="old",
            ai_summary="old summary",
            ats_score=40,
            ats_score_source="old",
            ats_summary="old ats",
            ats_breakdown_json=json.dumps({"before": True}),
            semantic_input_source="old",
            semantic_input_chars=12,
            semantic_chunks=1,
            semantic_fallback_reason=None,
            keyword_source="old",
            thread_snapshot_used=False,
            thread_snapshot_email_id=None,
            skip_reason=None,
            sync_batch_id=None,
            draft_reply="Old draft",
            draft_source="rules_only",
            draft_model=None,
            draft_ai_error="old ai error",
            draft_resume_context_status="rules_only",
            approval_status="pending",
            sent_status="not_sent",
            source=source,
            external_message_id=external_message_id or f"{source}-msg-1",
            external_thread_id="https://www.nvoids.com/job_details.jsp?id=123" if source == "nvoids" else "gmail-thread-1",
            external_rfc_message_id=None,
            gmail_received_at=now,
            recipient_email="saved-to@example.com",
            cc_email="saved-cc@example.com",
            routing_status="confirmed" if routing_confirmed else "safe",
            routing_confidence=1.0 if routing_confirmed else 0.91,
            routing_reason="saved route",
            routing_evidence="[]",
            routing_candidates="[]",
            routing_confirmed=routing_confirmed,
            resume_asset_id=None,
            resume_file_name="resume-before.pdf",
            parser_details_json=json.dumps({"parser_version": "before"}),
            last_error="before error",
            created_at=now,
            updated_at=now,
        )
        db.add(email)
        db.commit()
        db.refresh(email)
        return email

    def test_regenerate_gmail_refreshes_pipeline_outputs_and_clears_ai_error(self) -> None:
        original_parse_with_details = main.parse_email_with_details
        original_select_best_resume_match = main._select_best_resume_match
        original_compute_blended = main._compute_blended_ai_score
        original_hard_filter_check = main.hard_filter_check
        original_generate_reply = main.generate_reply_with_ai_or_fallback
        original_eval_routing = main._evaluate_routing_policy
        try:
            with Session(self.engine) as db:
                resume = self._add_resume(db, file_name="resume-current.pdf")
                selected_resume = self._resume_like(resume)
                email = self._add_email(db, source="gmail")

            main.parse_email_with_details = lambda subject, body, **kwargs: (
                {
                    "role": "Regenerated Role",
                    "location": "Remote",
                    "salary_text": "$80/hr",
                    "skills_text": "Java, Spring Boot",
                },
                {"parser_version": "regen-v1", "skills_audit": {"unknown": ["ProprietaryNebulaGateway"]}},
            )
            main._select_best_resume_match = lambda **kwargs: SimpleNamespace(
                resume=selected_resume,
                ai_score=0.91,
                ai_summary="fresh summary",
                ai_score_source="v2_rules_plus_semantic",
                ats_score=88.0,
                ats_score_source="hybrid_structured_plus_semantic",
                ats_summary="ATS improved",
                ats_breakdown_json=json.dumps({"matched": ["Java"]}),
                email_embedding_json='{"embedding":"email"}',
                resume_embedding_json='{"embedding":"resume"}',
                semantic_diag=SimpleNamespace(
                    input_source="chunked",
                    input_chars=321,
                    chunks=2,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main._compute_blended_ai_score = lambda **kwargs: (
                0.91,
                "fresh summary",
                "v2_rules_plus_semantic",
                '{"embedding":"email"}',
                '{"embedding":"resume"}',
                SimpleNamespace(
                    input_source="chunked",
                    input_chars=321,
                    chunks=2,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main.hard_filter_check = lambda *_args, **_kwargs: (True, "")
            main._evaluate_routing_policy = lambda *_args, **_kwargs: RoutingDecision(
                to_email="fresh-to@example.com",
                cc_email="fresh-cc@example.com",
                status="safe",
                confidence=0.95,
                reason="fresh routing",
                evidence=[],
                candidates=[],
                recommended_state="needs_review",
                recommended_skip_reason=None,
                should_mark_failed=False,
                is_sendable_candidate=True,
                needs_manual_confirmation=False,
            )
            main.generate_reply_with_ai_or_fallback = lambda **kwargs: SimpleNamespace(
                draft_text="Fresh AI draft",
                source="ai_primary",
                ai_model="mock-model",
                ai_error=None,
                resume_context_status="full",
            )

            response = self.client.post(
                f"/candidates/{email.id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["role"], "Regenerated Role")
            self.assertEqual(payload["draft_reply"], "Fresh AI draft")
            self.assertEqual(payload["draft_ai_error"], None)
            self.assertEqual(payload["recipient_email"], "fresh-to@example.com")
            self.assertEqual(payload["parser_details"]["parser_version"], "regen-v1")

            with Session(self.engine) as db:
                refreshed = db.get(RecruiterEmail, email.id)
                assert refreshed is not None
                self.assertEqual(refreshed.state, "needs_review")
                self.assertEqual(refreshed.resume_file_name, "resume-current.pdf")
                self.assertEqual(refreshed.ats_score, 88.0)
                self.assertEqual(
                    json.loads(refreshed.skills_json or "{}")["unknown"],
                    ["ProprietaryNebulaGateway"],
                )
        finally:
            main.parse_email_with_details = original_parse_with_details
            main._select_best_resume_match = original_select_best_resume_match
            main._compute_blended_ai_score = original_compute_blended
            main.hard_filter_check = original_hard_filter_check
            main.generate_reply_with_ai_or_fallback = original_generate_reply
            main._evaluate_routing_policy = original_eval_routing

    def test_regenerate_gmail_cleans_legacy_html_before_pipeline(self) -> None:
        with Session(self.engine) as db:
            resume = self._add_resume(db, file_name="resume-current.pdf")
            selected_resume = self._resume_like(resume)
            email = self._add_email(
                db,
                source="gmail",
                body=(
                    "<!doctype html><html><head><style>.hidden{display:none}</style></head>"
                    "<body><h1>Java Engineer</h1><p>Contact recruiter@example.com</p></body></html>"
                ),
            )
            email_id = email.id

        parsed_bodies: list[str] = []
        semantic_diag = SimpleNamespace(
            input_source="chunked",
            input_chars=100,
            chunks=1,
            fallback_reason=None,
            keyword_source="jd_only",
            thread_snapshot_used=False,
            thread_snapshot_email_id=None,
        )

        def parse_email(_subject: str, body: str, **_kwargs):
            parsed_bodies.append(body)
            return (
                {
                    "role": "Java Engineer",
                    "location": "Remote",
                    "salary_text": "not_specified",
                    "skills_text": "Java",
                },
                {"parser_version": "legacy-html"},
            )

        with (
            patch.object(main, "parse_email_with_details", side_effect=parse_email),
            patch.object(main, "_select_best_resume_match", return_value=SimpleNamespace(resume=selected_resume)),
            patch.object(
                main,
                "_compute_blended_ai_score",
                return_value=(0.91, "summary", "test", None, None, semantic_diag),
            ),
            patch.object(main, "hard_filter_check", return_value=(True, "")),
            patch.object(
                main,
                "_evaluate_routing_policy",
                return_value=RoutingDecision(
                    to_email="fresh-to@example.com",
                    cc_email="fresh-cc@example.com",
                    status="safe",
                    confidence=0.95,
                    reason="fresh routing",
                    evidence=[],
                    candidates=[],
                    recommended_state="needs_review",
                    recommended_skip_reason=None,
                    should_mark_failed=False,
                    is_sendable_candidate=True,
                    needs_manual_confirmation=False,
                ),
            ),
            patch.object(
                main,
                "generate_reply_with_ai_or_fallback",
                return_value=SimpleNamespace(
                    draft_text="Fresh draft",
                    source="rules_only",
                    ai_model=None,
                    ai_error=None,
                    resume_context_status="rules_only",
                ),
            ),
        ):
            response = self.client.post(
                f"/candidates/{email_id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["draft_reply"], "Fresh draft")
        self.assertEqual(len(parsed_bodies), 1)
        self.assertNotIn("<!doctype", parsed_bodies[0].lower())
        self.assertNotIn("display:none", parsed_bodies[0].lower())
        self.assertIn("Java Engineer", parsed_bodies[0])

    def test_regenerate_manifest_retry_cleans_legacy_gmail_html_before_detection(self) -> None:
        with Session(self.engine) as db:
            user_settings = db.query(UserSettings).one()
            user_settings.feature_role_manifest_enabled = True
            email = self._add_email(
                db,
                source="gmail",
                body=(
                    "<!doctype html><html><head><style>.hidden{display:none}</style></head>"
                    "<body><h1>Java Engineer</h1><p>Single role requirement</p></body></html>"
                ),
            )
            email.sendability_status = "manifest_review"
            email.draft_reply = ""
            email.draft_source = None
            db.commit()
            email_id = email.id

        detected_bodies: list[str] = []
        manifest_result = RoleManifestResult(
            status="single",
            manifest=RoleManifest(
                classification="single",
                role_count=1,
                confidence=0.95,
                shared_constraints=[],
                roles=[],
            ),
        )

        with (
            patch.object(main, "RoleManifestService") as manifest_service_type,
            patch.object(main, "extract_and_score_children") as extract_children,
        ):
            manifest_service_type.return_value.detect.side_effect = (
                lambda body: detected_bodies.append(body) or manifest_result
            )
            response = self.client.post(
                f"/candidates/{email_id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(detected_bodies), 1)
        self.assertNotIn("<!doctype", detected_bodies[0].lower())
        self.assertNotIn("display:none", detected_bodies[0].lower())
        self.assertIn("Java Engineer", detected_bodies[0])
        extract_children.assert_called_once()

    def test_regenerate_requires_confirmation_before_multi_role_fork(self) -> None:
        with Session(self.engine) as db:
            user_settings = db.query(UserSettings).one()
            user_settings.feature_role_manifest_enabled = True
            email = self._add_email(db, source="gmail", body="1. Java Engineer\n2. Data Engineer")
            record = CandidateRecord(id="fork-parent-record", owner_id=main.settings.owner_id, origin_type="gmail")
            lineage = OpportunityLineage(
                id="fork-parent-lineage",
                owner_id=main.settings.owner_id,
                origin_type="gmail",
            )
            db.add_all([record, lineage])
            db.flush()
            record.internal_lineage_id = lineage.id
            email.record_id = record.id
            db.commit()
            email_id = email.id

        requirements = (
            MaterializedRequirement(1, "Java Engineer", "", 1, 1, "Java Engineer", "java"),
            MaterializedRequirement(2, "Data Engineer", "", 2, 2, "Data Engineer", "data"),
        )
        manifest_result = RoleManifestResult(
            status="multiple",
            manifest=RoleManifest(
                classification="multiple",
                role_count=2,
                confidence=0.95,
                roles=[],
            ),
            requirements=requirements,
        )
        with (
            patch.object(main, "RoleManifestService") as manifest_service_type,
            patch.object(main.settings, "role_manifest_child_creation_enabled", True),
            patch.object(main, "extract_and_score_children"),
            patch.object(main, "_enqueue_embedding_generation"),
        ):
            manifest_service_type.return_value.detect.return_value = manifest_result
            blocked = self.client.post(
                f"/candidates/{email_id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )
            confirmed = self.client.post(
                f"/candidates/{email_id}/regenerate",
                json={
                    "preserve_manual_routing": True,
                    "preserve_review_visibility": True,
                    "allow_role_manifest_fork": True,
                },
            )

        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertEqual(blocked.json()["detail"], {"code": "role_manifest_fork_required", "requirement_count": 2})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        with Session(self.engine) as db:
            children = db.query(RecruiterEmail).filter_by(source_parent_email_id=email_id).all()
            events = db.query(OpportunityLifecycleEvent).filter_by(
                lineage_id="fork-parent-lineage",
                event_type="forked_into_requirement",
            ).all()
            self.assertEqual(len(children), 2)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                {json.loads(event.metadata_json)["child_record_id"] for event in events},
                {child.record_id for child in children},
            )

    def test_regenerate_preserves_manual_routing_when_confirmed(self) -> None:
        original_parse_with_details = main.parse_email_with_details
        original_select_best_resume_match = main._select_best_resume_match
        original_compute_blended = main._compute_blended_ai_score
        original_hard_filter_check = main.hard_filter_check
        original_generate_reply = main.generate_reply_with_ai_or_fallback
        original_eval_routing = main._evaluate_routing_policy
        try:
            with Session(self.engine) as db:
                resume = self._add_resume(db)
                selected_resume = self._resume_like(resume)
                email = self._add_email(db, routing_confirmed=True)
                # routing_confirmed=True alone isn't a genuine manual-confirmation signal (it's
                # also set as a side effect of a prior successful auto-derived routing pass) --
                # only routing_evidence sourced "manual_edit" (written by the real
                # resolve-recipients endpoint) should make regenerate preserve routing verbatim.
                email.routing_evidence = json.dumps(
                    [
                        {"role": "to", "email": "saved-to@example.com", "source": "manual_edit", "detail": "Confirmed by user"},
                        {"role": "cc", "email": "saved-cc@example.com", "source": "manual_edit", "detail": "Confirmed by user"},
                    ]
                )
                db.commit()
                db.refresh(email)

            def _unexpected_routing(*_args, **_kwargs):
                raise AssertionError("routing evaluation should be skipped for confirmed routing")

            main.parse_email_with_details = lambda subject, body, **kwargs: (
                {"role": "Role", "location": "Remote", "salary_text": "", "skills_text": "Java"},
                {"parser_version": "regen-v1"},
            )
            main._select_best_resume_match = lambda **kwargs: SimpleNamespace(
                resume=selected_resume,
                ai_score=0.84,
                ai_summary="ok",
                ai_score_source="v2",
                ats_score=75.0,
                ats_score_source="hybrid",
                ats_summary="ok",
                ats_breakdown_json=json.dumps({"matched": ["Java"]}),
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(
                    input_source="latest_block",
                    input_chars=100,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main._compute_blended_ai_score = lambda **kwargs: (
                0.84,
                "ok",
                "v2",
                None,
                None,
                SimpleNamespace(
                    input_source="latest_block",
                    input_chars=100,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main.hard_filter_check = lambda *_args, **_kwargs: (True, "")
            main.generate_reply_with_ai_or_fallback = lambda **kwargs: SimpleNamespace(
                draft_text="Preserved routing draft",
                source="ai_primary",
                ai_model="mock-model",
                ai_error=None,
                resume_context_status="full",
            )
            main._evaluate_routing_policy = _unexpected_routing

            response = self.client.post(
                f"/candidates/{email.id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["recipient_email"], "saved-to@example.com")
            self.assertEqual(payload["cc_email"], "saved-cc@example.com")
            self.assertTrue(payload["routing_confirmed"])
        finally:
            main.parse_email_with_details = original_parse_with_details
            main._select_best_resume_match = original_select_best_resume_match
            main._compute_blended_ai_score = original_compute_blended
            main.hard_filter_check = original_hard_filter_check
            main.generate_reply_with_ai_or_fallback = original_generate_reply
            main._evaluate_routing_policy = original_eval_routing

    def test_regenerate_not_qualified_stays_in_needs_review_when_visibility_preserved(self) -> None:
        original_parse_with_details = main.parse_email_with_details
        original_select_best_resume_match = main._select_best_resume_match
        original_compute_blended = main._compute_blended_ai_score
        original_hard_filter_check = main.hard_filter_check
        try:
            with Session(self.engine) as db:
                resume = self._add_resume(db)
                selected_resume = self._resume_like(resume)
                email = self._add_email(db, source="gmail")

            main.parse_email_with_details = lambda subject, body, **kwargs: (
                {"role": "Role", "location": "Remote", "salary_text": "", "skills_text": "Java"},
                {"parser_version": "regen-v1"},
            )
            main._select_best_resume_match = lambda **kwargs: SimpleNamespace(
                resume=selected_resume,
                ai_score=0.15,
                ai_summary="too low",
                ai_score_source="v2",
                ats_score=22.0,
                ats_score_source="hybrid",
                ats_summary="weak",
                ats_breakdown_json=json.dumps({"matched": []}),
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(
                    input_source="latest_block",
                    input_chars=100,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main._compute_blended_ai_score = lambda **kwargs: (
                0.15,
                "too low",
                "v2",
                None,
                None,
                SimpleNamespace(
                    input_source="latest_block",
                    input_chars=100,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main.hard_filter_check = lambda *_args, **_kwargs: (True, "")

            response = self.client.post(
                f"/candidates/{email.id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(payload["state"], "needs_review")
            self.assertEqual(payload["decision"], "Reject")
            self.assertEqual(payload["auto_reject_reason"], "ai_score_too_low")
            self.assertIn("no longer qualified", payload["last_error"])
            self.assertEqual(payload["draft_reply"], "Hi")
            self.assertEqual(payload["draft_source"], "rules_only")
            self.assertEqual(payload["draft_resume_context_status"], "rules_only")
            self.assertEqual(payload["sendability_status"], "score_review")
        finally:
            main.parse_email_with_details = original_parse_with_details
            main._select_best_resume_match = original_select_best_resume_match
            main._compute_blended_ai_score = original_compute_blended
            main.hard_filter_check = original_hard_filter_check

    def test_regenerate_blocks_terminal_states(self) -> None:
        with Session(self.engine) as db:
            email = self._add_email(db, state="approved_sent")

        response = self.client.post(
            f"/candidates/{email.id}/regenerate",
            json={"preserve_manual_routing": True, "preserve_review_visibility": True},
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "Candidate is in terminal state")

    def test_regenerate_nvoids_reuses_external_context_and_recomputes_unconfirmed_routing(self) -> None:
        original_parse_with_details = main.parse_email_with_details
        original_select_best_resume_match = main._select_best_resume_match
        original_compute_blended = main._compute_blended_ai_score
        original_hard_filter_check = main.hard_filter_check
        original_generate_reply = main.generate_reply_with_ai_or_fallback
        original_eval_routing = main._evaluate_routing_policy
        original_parse_nvoids_detail = orchestration_module.parse_nvoids_detail
        captured_kwargs: dict[str, object] = {}
        try:
            with Session(self.engine) as db:
                resume = self._add_resume(db)
                selected_resume = self._resume_like(resume)
                source = self._add_feed_source(db)
                self._add_external_opportunity(db, feed_source_id=source.id, external_post_id="post-123")
                email = self._add_email(db, source="nvoids", external_message_id="nvoids:post-123")

            orchestration_module.parse_nvoids_detail = lambda raw_html, title, location: SimpleNamespace(
                jd_body="JD BODY FROM HTML",
                jd_body_source="nvoids_detail_table_row_3",
            )

            def _parse_with_details(subject, body, **kwargs):
                captured_kwargs.update(kwargs)
                return (
                    {
                        "role": "Senior Java Developer",
                        "location": "Remote, USA",
                        "salary_text": "$70/hr",
                        "skills_text": "Java, Spring Boot",
                    },
                    {"parser_version": "regen-nvoids-v1"},
                )

            main.parse_email_with_details = _parse_with_details
            main._select_best_resume_match = lambda **kwargs: SimpleNamespace(
                resume=selected_resume,
                ai_score=0.86,
                ai_summary="nvoids ok",
                ai_score_source="v2",
                ats_score=81.0,
                ats_score_source="hybrid",
                ats_summary="good",
                ats_breakdown_json=json.dumps({"matched": ["Java"]}),
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(
                    input_source="nvoids_detail_table_row_3",
                    input_chars=210,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main._compute_blended_ai_score = lambda **kwargs: (
                0.86,
                "nvoids ok",
                "v2",
                None,
                None,
                SimpleNamespace(
                    input_source="nvoids_detail_table_row_3",
                    input_chars=210,
                    chunks=1,
                    fallback_reason=None,
                    keyword_source="jd_only",
                    thread_snapshot_used=False,
                    thread_snapshot_email_id=None,
                ),
            )
            main.hard_filter_check = lambda *_args, **_kwargs: (True, "")
            main.generate_reply_with_ai_or_fallback = lambda **kwargs: SimpleNamespace(
                draft_text="Nvoids AI draft",
                source="ai_primary",
                ai_model="mock-model",
                ai_error=None,
                resume_context_status="full",
            )
            # Stored routing here is system-derived (routing_confirmed=False, no manual_edit
            # evidence), not a genuine human confirmation, so regenerate must recompute it fresh
            # rather than blindly preserving it forever -- preserving unconfirmed nvoids routing
            # unconditionally is exactly what let a stale/unrelated CC address survive regenerate
            # indefinitely (Email 6159).
            main._evaluate_routing_policy = lambda *_args, **_kwargs: RoutingDecision(
                to_email="fresh-nvoids-to@example.com",
                cc_email="fresh-nvoids-cc@example.com",
                status="safe",
                confidence=0.95,
                reason="fresh nvoids routing",
                evidence=[],
                candidates=[],
                recommended_state="needs_review",
                recommended_skip_reason=None,
                should_mark_failed=False,
                is_sendable_candidate=True,
                needs_manual_confirmation=False,
            )

            response = self.client.post(
                f"/candidates/{email.id}/regenerate",
                json={"preserve_manual_routing": True, "preserve_review_visibility": True},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(captured_kwargs.get("source"), "nvoids")
            self.assertEqual(captured_kwargs.get("ai_body_override"), "JD BODY FROM HTML")
            source_hints = captured_kwargs.get("source_hints")
            self.assertIsInstance(source_hints, dict)
            assert isinstance(source_hints, dict)
            self.assertEqual(source_hints.get("canonical_title"), "Senior Java Developer")
            self.assertEqual(source_hints.get("ai_input_source"), "nvoids_detail_table_row_3")
            self.assertEqual(response.json()["recipient_email"], "fresh-nvoids-to@example.com")
            self.assertEqual(response.json()["cc_email"], "fresh-nvoids-cc@example.com")
            self.assertEqual(response.json()["state"], "needs_review")
        finally:
            main.parse_email_with_details = original_parse_with_details
            main._select_best_resume_match = original_select_best_resume_match
            main._compute_blended_ai_score = original_compute_blended
            main.hard_filter_check = original_hard_filter_check
            main.generate_reply_with_ai_or_fallback = original_generate_reply
            main._evaluate_routing_policy = original_eval_routing
            orchestration_module.parse_nvoids_detail = original_parse_nvoids_detail


if __name__ == "__main__":
    unittest.main()
