import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation import RunOrchestrator, RunOrchestratorDependencies, RunOrchestratorRequest
from app.db import Base
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.phase0 import RoutingEvidence, RoutingResult


class RunOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _seed_user_settings(
        self,
        db: Session,
        *,
        feature_ai_enabled: bool,
        feature_ai_extractor_enabled: bool = False,
    ) -> UserSettings:
        user_settings = UserSettings(
            owner_id="default-owner",
            enabled=True,
            gmail_query="is:unread",
            default_gmail_query="is:unread",
            default_date_mode="off",
            qualification_threshold=0.6,
            feature_ai_enabled=feature_ai_enabled,
            feature_ai_extractor_enabled=feature_ai_extractor_enabled,
            feature_semantic_enabled=False,
            fallback_draft_template="Hi",
            signature_name="Tester",
            signature_phone="+1",
            signature_email="tester@example.com",
            policy_json="",
        )
        db.add(user_settings)
        db.commit()
        db.refresh(user_settings)
        return user_settings

    def _seed_resume(self, db: Session) -> ResumeAsset:
        resume = ResumeAsset(
            owner_id="default-owner",
            file_path="resume.pdf",
            file_name="resume.pdf",
            mime_type="application/pdf",
            sha256="abc123",
            version=1,
            is_current=True,
            semantic_embedding=None,
        )
        db.add(resume)
        db.commit()
        db.refresh(resume)
        return resume

    def _item(self, message_id: str = "m-1") -> dict[str, object]:
        return {
            "sender": "Recruiter <r@example.com>",
            "subject": "Java role",
            "body": "Body",
            "snippet": "Body",
            "external_message_id": message_id,
            "external_thread_id": "t-1",
            "external_rfc_message_id": "rfc-1",
            "gmail_received_at": datetime.now(UTC),
            "recipient_email": "legacy-to@example.com",
        }

    def _deps(
        self,
        *,
        hard_pass: bool = True,
        ai_score: float = 0.9,
        blocked: bool = False,
        to_email: str | None = "to@example.com",
        cc_email: str | None = "cc@example.com",
    ) -> tuple[RunOrchestratorDependencies, list[str], list[tuple[str, str]]]:
        marked: list[str] = []
        events: list[tuple[str, str]] = []

        def parse_email(_subject: str, _body: str) -> dict[str, str]:
            return {
                "role": "Java Developer",
                "location": "hybrid",
                "job_location_text": "hybrid",
                "salary_text": "$60/hr",
                "skills_text": "java",
                "f2f_mentioned": False,
                "asks_contact_fields": False,
                "is_texas_role": False,
            }

        def parse_email_with_details(_subject: str, _body: str, **_kwargs: object) -> tuple[dict[str, str], dict[str, object]]:
            parsed = parse_email(_subject, _body)
            return parsed, {
                "parser_version": "spacy_enrichment_v1",
                "source": "gmail",
                "base_parser_result": dict(parsed),
                "enrichment_result": {},
                "ai_extractor_result": None,
                "merged_result": dict(parsed),
                "merge_notes": [],
                "ai_merge_notes": [],
                "source_hints": {},
            }

        def hard_filter_check(_parsed: dict[str, str | int | bool], _user_settings: UserSettings) -> tuple[bool, str]:
            return hard_pass, "hard_fail" if not hard_pass else "pass"

        def compute_blended(
            _subject: str,
            _body: str,
            _parsed: dict[str, str | int | bool],
            _user_settings: UserSettings,
            _existing: RecruiterEmail | None,
            _resume: ResumeAsset | None,
            *_ctx: object,
        ) -> tuple[float, str, str, str | None, str | None, object]:
            diag = SimpleNamespace(input_source="latest_block", input_chars=120, chunks=1, fallback_reason=None)
            return ai_score, "summary", "v1", None, None, diag

        def policy_f2f_block(
            _parsed: dict[str, str | int | bool], _policy: dict[str, object]
        ) -> tuple[bool, str]:
            return (True, "blocked") if blocked else (False, "")

        def evaluate_routing_policy(
            _db: Session, _sender: str, _subject: str, _body: str, _snippet: str, _routing_confirmed: bool
        ) -> object:
            evidence = []
            if to_email:
                evidence.append(RoutingEvidence(role="to", email=to_email, source="test", detail="to"))
            if cc_email:
                evidence.append(RoutingEvidence(role="cc", email=cc_email, source="test", detail="cc"))
            return SimpleNamespace(
                to_email=to_email,
                cc_email=cc_email,
                status="safe" if to_email and cc_email else "missing",
                confidence=0.9 if to_email and cc_email else 0.0,
                reason="test",
                evidence=evidence,
                candidates=evidence,
                recommended_state="failed",
                recommended_skip_reason="missing_to_or_cc" if not (to_email and cc_email) else None,
                should_mark_failed=not (to_email and cc_email),
            )

        def build_user_fallback_draft(
            _db: Session,
            _user_settings: UserSettings,
            _sender: str,
            _role: str,
            _parsed: dict[str, str | int | bool],
            _greeting_line: str,
            _resume_file_name: str | None,
        ) -> str:
            return "fallback"

        def apply_routing_decision(email: RecruiterEmail, routing: object) -> None:
            email.recipient_email = routing.to_email
            email.cc_email = routing.cc_email
            email.routing_status = routing.status
            email.routing_confidence = routing.confidence
            email.routing_reason = routing.reason
            email.routing_evidence = "[]"
            email.routing_candidates = "[]"

        def record_productivity_event(
            _db: Session, *, event_type: str, event_source: str, **_kwargs: object
        ) -> None:
            events.append((event_type, event_source))

        def select_best_resume_match(**kwargs: object) -> object:
            resume = kwargs.get("fallback_resume")
            return SimpleNamespace(
                resume=resume,
                ai_score=ai_score,
                ai_summary="summary",
                ai_score_source="v1",
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(input_source="latest_block", input_chars=120, chunks=1, fallback_reason=None),
            )

        deps = RunOrchestratorDependencies(
            parse_email=parse_email,
            parse_email_with_details=parse_email_with_details,
            hard_filter_check=hard_filter_check,
            compute_blended_ai_score=compute_blended,
            policy_f2f_block=policy_f2f_block,
            evaluate_routing_policy=evaluate_routing_policy,
            greeting_from_to_contact=lambda _to, _body: "Hi Recruiter,",
            build_user_fallback_draft=build_user_fallback_draft,
            generate_reply_with_ai_or_fallback=lambda **_kwargs: SimpleNamespace(
                draft_text="ai draft",
                source="deepseek",
                ai_model="deepseek-chat",
                ai_error=None,
                resume_context_status="injected",
            ),
            apply_routing_decision=apply_routing_decision,
            select_best_resume_match=select_best_resume_match,
            capture_premium_numbers=lambda *_args, **_kwargs: None,
            record_productivity_event=record_productivity_event,
            apply_gmail_label=lambda *_args, **_kwargs: None,
            mark_message_processed=lambda message_id: marked.append(message_id),
        )
        return deps, marked, events

    def test_existing_approved_sent_is_skipped_and_marked(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            db.add(
                RecruiterEmail(
                    owner_id="default-owner",
                    sender="r@example.com",
                    subject="Role",
                    body="Body",
                    role="Java",
                    location="hybrid",
                    salary_text="",
                    skills_text="java",
                    score=90,
                    decision="Qualified",
                    state="approved_sent",
                    draft_reply="draft",
                    source="gmail",
                    external_message_id="m-1",
                    external_thread_id="t-1",
                )
            )
            db.commit()
            deps, marked, _events = self._deps()
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-1")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(marked, ["m-1"])

    def test_dry_run_does_not_mutate_database_or_mark_processed(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps(hard_pass=False, ai_score=0.1)
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-2")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=True,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-2").first()
            self.assertIsNone(row)
            self.assertEqual(marked, [])
            self.assertEqual(result.skipped_count, 1)

    def test_not_qualified_path_persists_processed_skipped(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps(hard_pass=False, ai_score=0.1)
            RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-3")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-3").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "processed_skipped")
            self.assertEqual(marked, ["m-3"])

    def test_explicit_interview_block_skips_before_draft_generation(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=True)
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps(blocked=True)
            RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-3b")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-3b").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "processed_skipped")
            self.assertEqual(row.auto_reject_reason, "f2f_non_texas")
            self.assertEqual(row.skip_reason, "f2f_non_texas_blocked")
            self.assertEqual(row.decision, "Reject")
            self.assertEqual(row.decision_reason, "blocked")
            self.assertIsNone(row.draft_source)
            self.assertEqual(row.draft_reply, "")
            self.assertEqual(marked, ["m-3b"])

    def test_routing_missing_path_persists_failed_and_event(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, events = self._deps(to_email=None, cc_email=None)
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-4")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-4").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "failed")
            self.assertEqual(result.failed_count, 1)
            self.assertEqual(marked, ["m-4"])
            self.assertIn(("failed_mapping_marked", "state"), events)

    def test_qualified_rules_only_path_queues_candidate(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, events = self._deps()
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-5")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-5").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "needs_review")
            self.assertEqual(row.draft_source, "rules_only")
            self.assertEqual(result.queued_count, 1)
            self.assertEqual(marked, ["m-5"])
            self.assertIn(("needs_review_marked", "state"), events)

    def test_gmail_path_reuses_single_parse_with_details_when_ai_extractor_enabled(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(
                db,
                feature_ai_enabled=False,
                feature_ai_extractor_enabled=True,
            )
            resume = self._seed_resume(db)
            marked: list[str] = []
            events: list[tuple[str, str]] = []
            parse_email_calls: list[tuple[str, str]] = []
            parse_email_with_details_calls: list[tuple[str, str, bool]] = []

            def parse_email(subject: str, body: str) -> dict[str, str | int | bool]:
                parse_email_calls.append((subject, body))
                return {
                    "role": "Base Role",
                    "location": "Base Location",
                    "job_location_text": "Base Location",
                    "salary_text": "$60/hr",
                    "skills_text": "java",
                    "f2f_mentioned": False,
                    "asks_contact_fields": False,
                    "is_texas_role": False,
                }

            def parse_email_with_details(subject: str, body: str, **kwargs: object) -> tuple[dict[str, str | int | bool], dict[str, object]]:
                parse_email_with_details_calls.append((subject, body, bool(kwargs.get("ai_extractor_enabled"))))
                parsed = {
                    "role": "AI Enriched Role",
                    "location": "Dallas, TX",
                    "job_location_text": "Dallas, TX",
                    "salary_text": "$60/hr",
                    "skills_text": "Java, Amazon ECS",
                    "f2f_mentioned": False,
                    "asks_contact_fields": False,
                    "is_texas_role": True,
                }
                return parsed, {
                    "parser_version": "spacy_ai_enrichment_v2",
                    "source": "gmail",
                    "base_parser_result": {"role": "Base Role"},
                    "enrichment_result": {},
                    "ai_extractor_result": {"skills_approved": ["Java", "Amazon ECS"]},
                    "merged_result": dict(parsed),
                    "merge_notes": [],
                    "ai_merge_notes": ["merged approved AI extractor skills into taxonomy-normalized skills_text"],
                    "source_hints": {},
                }

            deps = RunOrchestratorDependencies(
                parse_email=parse_email,
                parse_email_with_details=parse_email_with_details,
                hard_filter_check=lambda *_args, **_kwargs: (True, "pass"),
                compute_blended_ai_score=lambda *_args, **_kwargs: (
                    0.9,
                    "summary",
                    "v1",
                    None,
                    None,
                    SimpleNamespace(input_source="latest_block", input_chars=120, chunks=1, fallback_reason=None),
                ),
                policy_f2f_block=lambda *_args, **_kwargs: (False, ""),
                evaluate_routing_policy=lambda *_args, **_kwargs: SimpleNamespace(
                    to_email="to@example.com",
                    cc_email="cc@example.com",
                    status="safe",
                    confidence=0.9,
                    reason="test",
                    evidence=[],
                    candidates=[],
                    recommended_state="failed",
                    recommended_skip_reason=None,
                    should_mark_failed=False,
                ),
                greeting_from_to_contact=lambda _to, _body: "Hi Recruiter,",
                build_user_fallback_draft=lambda *_args, **_kwargs: "fallback",
                generate_reply_with_ai_or_fallback=lambda **_kwargs: SimpleNamespace(
                    draft_text="ai draft",
                    source="deepseek",
                    ai_model="deepseek-chat",
                    ai_error=None,
                    resume_context_status="injected",
                ),
                apply_routing_decision=lambda email, routing: (
                    setattr(email, "recipient_email", routing.to_email),
                    setattr(email, "cc_email", routing.cc_email),
                    setattr(email, "routing_status", routing.status),
                    setattr(email, "routing_confidence", routing.confidence),
                    setattr(email, "routing_reason", routing.reason),
                    setattr(email, "routing_evidence", "[]"),
                    setattr(email, "routing_candidates", "[]"),
                ),
                select_best_resume_match=lambda **kwargs: SimpleNamespace(
                    resume=kwargs.get("fallback_resume"),
                    ai_score=0.9,
                    ai_summary="summary",
                    ai_score_source="v1",
                    email_embedding_json=None,
                    resume_embedding_json=None,
                    semantic_diag=SimpleNamespace(input_source="latest_block", input_chars=120, chunks=1, fallback_reason=None),
                ),
                capture_premium_numbers=lambda *_args, **_kwargs: None,
                record_productivity_event=lambda _db, *, event_type, event_source, **_kwargs: events.append((event_type, event_source)),
                apply_gmail_label=lambda *_args, **_kwargs: None,
                mark_message_processed=lambda message_id: marked.append(message_id),
            )

            RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-ai-1")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )

            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-ai-1").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(parse_email_calls, [])
            self.assertEqual(parse_email_with_details_calls, [("Java role", "Body", True)])
            self.assertEqual(row.role, "AI Enriched Role")
            self.assertIn("Amazon ECS", row.skills_text)
            self.assertIn("spacy_ai_enrichment_v2", row.parser_details_json or "")
            self.assertEqual(marked, ["m-ai-1"])


if __name__ == "__main__":
    unittest.main()
