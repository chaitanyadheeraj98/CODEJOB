import unittest
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.automation import RunOrchestrator, RunOrchestratorDependencies, RunOrchestratorRequest
from app.db import Base
from app.gates import EmailIntentDecision
from app.models import RecentRunSkippedItem, RecruiterEmail, ResumeAsset, UserSettings
from app.phase0 import RoutingEvidence, RoutingResult
from app.recent_runs import record_skipped_item
from app.services import policy_service


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
            feature_groq_job_parser_enabled=False,
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
        recruiter_like: bool = True,
        intent_decision: EmailIntentDecision | None = None,
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
                "parser_version": "base_parser_v1",
                "source": "gmail",
                "base_parser_result": dict(parsed),
                "ai_extractor_result": None,
                "approved_skills_text": str(parsed["skills_text"]),
                "unknown_skills": [],
                "merged_result": dict(parsed),
                "ai_merge_notes": [],
                "source_hints": {},
            }

        def hard_filter_check(
            _parsed: dict[str, str | int | bool],
            _user_settings: UserSettings,
            _policy: dict[str, object],
            _parser_details=None,
        ) -> tuple[bool, str]:
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
            is_recruiter_like=lambda *_args, **_kwargs: recruiter_like,
            classify_email_intent=lambda **_kwargs: intent_decision or EmailIntentDecision(
                intent_type="recruiter_job_requirement",
                action="process_for_queue",
                confidence=0.91,
                reason="Matched direct job-description structure.",
                evidence=["role_keyword", "location"],
                negative_evidence=[],
                provider="taxonomy",
            ),
            record_skipped_item=lambda db_ctx, payload: record_skipped_item(db_ctx, payload),
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
                    run_source="automation_run",
                    run_key="automation_run:test-1",
                    deps=deps,
                )
            )
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(marked, ["m-1"])
            skipped_item = db.query(RecentRunSkippedItem).filter(RecentRunSkippedItem.run_key == "automation_run:test-1").first()
            self.assertIsNotNone(skipped_item)
            assert skipped_item is not None
            self.assertEqual(skipped_item.reason_code, "approved_sent_duplicate")
            self.assertEqual(skipped_item.gmail_message_url, "https://mail.google.com/mail/u/0/#all/m-1")

    def test_strict_eligibility_mismatch_stops_before_resume_scoring_and_drafting(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=True)
            user_settings.feature_strict_candidate_screening_enabled = True
            user_settings.candidate_work_authorizations_json = '["H1B"]'
            db.commit()
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps()
            original_parse = deps.parse_email_with_details
            scoring_calls = {"count": 0}
            draft_calls = {"count": 0}

            def parse_with_usc_requirement(subject: str, body: str, **kwargs: object):
                parsed, details = original_parse(subject, body, **kwargs)
                details["structured_requirements"] = {
                    "allowed_work_authorizations": ["USC", "GC"],
                    "experience_years_min": 12,
                }
                return parsed, details

            def should_not_score(**_kwargs: object) -> object:
                scoring_calls["count"] += 1
                raise AssertionError("strict eligibility must run before resume scoring")

            def should_not_draft(**_kwargs: object) -> object:
                draft_calls["count"] += 1
                raise AssertionError("strict eligibility must run before drafting")

            strict_deps = replace(
                deps,
                parse_email_with_details=parse_with_usc_requirement,
                select_best_resume_match=should_not_score,
                generate_reply_with_ai_or_fallback=should_not_draft,
            )
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-strict-1")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    run_source="automation_run",
                    run_key="automation_run:strict",
                    deps=strict_deps,
                )
            )

            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-strict-1").one()
            self.assertEqual(result.queued_count, 1)
            self.assertEqual(scoring_calls["count"], 0)
            self.assertEqual(draft_calls["count"], 0)
            self.assertEqual(row.screening_mode, "strict")
            self.assertEqual(row.eligibility_status, "blocked")
            self.assertEqual(row.sendability_status, "blocked_ineligible")
            self.assertEqual(row.draft_reply, "")
            self.assertIsNone(row.resume_asset_id)
            self.assertEqual(marked, ["m-strict-1"])

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
                    run_source="automation_run",
                    run_key="automation_run:test-2",
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
                    run_source="automation_run",
                    run_key="automation_run:test-3",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-3").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "processed_skipped")
            self.assertEqual(row.qualification_result, "rejected")
            self.assertEqual(marked, ["m-3"])
            skipped_item = db.query(RecentRunSkippedItem).filter(RecentRunSkippedItem.run_key == "automation_run:test-3").first()
            self.assertIsNotNone(skipped_item)
            assert skipped_item is not None
            self.assertEqual(skipped_item.candidate_email_id, row.id)
            self.assertEqual(skipped_item.qualification_result, "rejected")

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
                    run_source="automation_run",
                    run_key="automation_run:test-4",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-3b").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "processed_skipped")
            self.assertEqual(row.auto_reject_reason, "f2f_non_texas")
            self.assertEqual(row.skip_reason, "f2f_non_texas_blocked")
            self.assertEqual(row.blocking_rule, "f2f_non_texas")
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
                    run_source="automation_run",
                    run_key="automation_run:test-5",
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
                    run_source="automation_run",
                    run_key="automation_run:test-6",
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

    def test_missing_to_and_cc_can_still_queue_when_recipient_mapping_rule_warns(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, events = self._deps(to_email=None, cc_email=None)
            policy = policy_service.default_policy()
            policy["qualification"]["draft_rules"]["recipient_mapping"]["mode"] = "warn"
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-5b")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy=policy,
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    run_source="automation_run",
                    run_key="automation_run:test-7",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-5b").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "needs_review")
            self.assertEqual(row.recipient_email, None)
            self.assertEqual(row.cc_email, None)
            self.assertEqual(row.hard_filter_result, "warnings: missing_to_or_cc")
            self.assertEqual(result.queued_count, 1)
            self.assertEqual(marked, ["m-5b"])
            self.assertIn(("needs_review_marked", "state"), events)

    def test_non_job_message_is_skipped_by_intent_gate(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps(
                recruiter_like=False,
                intent_decision=EmailIntentDecision(
                    intent_type="security_alert",
                    action="skip",
                    confidence=0.97,
                    reason="Matched account-security language rather than a job requirement.",
                    evidence=[],
                    negative_evidence=["security_alert:security alert"],
                    provider="taxonomy",
                ),
            )
            policy = policy_service.default_policy()
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-5c")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy=policy,
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    run_source="automation_run",
                    run_key="automation_run:test-8",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-5c").first()
            self.assertIsNone(row)
            self.assertEqual(result.skipped_count, 1)
            self.assertEqual(marked, [])
            skipped_item = db.query(RecentRunSkippedItem).filter(RecentRunSkippedItem.run_key == "automation_run:test-8").first()
            self.assertIsNotNone(skipped_item)
            assert skipped_item is not None
            self.assertEqual(skipped_item.reason_code, "security_alert")
            self.assertEqual(skipped_item.intent_type, "security_alert")
            self.assertEqual(skipped_item.gmail_message_url, "https://mail.google.com/mail/u/0/#all/m-5c")

    def test_real_job_message_still_processes_without_recruiter_words(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(db, feature_ai_enabled=False)
            resume = self._seed_resume(db)
            deps, marked, _events = self._deps(recruiter_like=False)
            policy = policy_service.default_policy()
            result = RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-5d")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy=policy,
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    run_source="automation_run",
                    run_key="automation_run:test-9",
                    deps=deps,
                )
            )
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-5d").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.state, "needs_review")
            self.assertEqual(row.hard_filter_result, "warnings: non_recruiter_like_gmail")
            self.assertEqual(row.intent_type, "recruiter_job_requirement")
            self.assertEqual(result.queued_count, 1)
            self.assertEqual(marked, ["m-5d"])

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
                    "parser_version": "base_ai_extractor_v1",
                    "source": "gmail",
                    "base_parser_result": {"role": "Base Role"},
                    "ai_extractor_result": {"skills_approved": ["Java", "Amazon ECS"]},
                    "approved_skills_text": "Java, Amazon ECS",
                    "unknown_skills": [],
                    "merged_result": dict(parsed),
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
                is_recruiter_like=lambda *_args, **_kwargs: True,
                classify_email_intent=lambda **_kwargs: EmailIntentDecision(
                    intent_type="recruiter_job_requirement",
                    action="process_for_queue",
                    confidence=0.92,
                    reason="Matched direct job-description structure.",
                    evidence=["job description"],
                    negative_evidence=[],
                    provider="taxonomy",
                ),
                record_skipped_item=lambda db_ctx, payload: record_skipped_item(db_ctx, payload),
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
                    run_source="automation_run",
                    run_key="automation_run:test-10",
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
            self.assertIsNotNone(row.skills_json)
            self.assertIn('"skills_text":"Java, Amazon ECS"', row.skills_json or "")
            self.assertIn('"known":["Java","Amazon ECS"]', row.skills_json or "")
            self.assertIn("base_ai_extractor_v1", row.parser_details_json or "")
            self.assertEqual(marked, ["m-ai-1"])

    def test_gmail_resume_selection_receives_merged_parse_output_when_ai_extractor_enabled(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._seed_user_settings(
                db,
                feature_ai_enabled=False,
                feature_ai_extractor_enabled=True,
            )
            resume = self._seed_resume(db)
            selection_kwargs: dict[str, object] = {}

            def parse_email(_subject: str, _body: str) -> dict[str, str | int | bool]:
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

            def parse_email_with_details(_subject: str, _body: str, **_kwargs: object) -> tuple[dict[str, str | int | bool], dict[str, object]]:
                parsed = {
                    "role": "Merged AI Role",
                    "location": "Dallas, TX",
                    "job_location_text": "Dallas, TX",
                    "salary_text": "$75/hr",
                    "skills_text": "Java, Amazon ECS",
                    "f2f_mentioned": False,
                    "asks_contact_fields": True,
                    "is_texas_role": True,
                }
                return parsed, {
                    "parser_version": "base_ai_extractor_v1",
                    "source": "gmail",
                    "base_parser_result": {"role": "Base Role"},
                    "ai_extractor_result": {"skills_approved": ["Java", "Amazon ECS"]},
                    "approved_skills_text": "Java, Amazon ECS",
                    "unknown_skills": [],
                    "merged_result": dict(parsed),
                    "ai_merge_notes": ["merged approved AI extractor skills into taxonomy-normalized skills_text"],
                    "source_hints": {},
                }

            def select_best_resume_match(**kwargs: object) -> object:
                selection_kwargs.update(kwargs)
                return SimpleNamespace(
                    resume=kwargs.get("fallback_resume"),
                    ai_score=0.9,
                    ai_summary="summary",
                    ai_score_source="v1",
                    final_resume_score=0.83,
                    selection_reason="Final 0.83; ai=0.90; ats=78.00",
                    candidate_rankings_json='{"rankings":[{"resume_file_name":"resume.pdf","final_resume_score":0.83}]}',
                    picker_breakdown_json='{"matched_priority_skills":["Java"],"missing_priority_skills":["Oracle"]}',
                    ats_score=78.0,
                    ats_score_source="hybrid_structured_only",
                    ats_summary="ATS hybrid score 78/100",
                    ats_breakdown_json='{"matched_raw_skills":["Java"],"missing_raw_skills":["Oracle"]}',
                    email_embedding_json=None,
                    resume_embedding_json=None,
                    semantic_diag=SimpleNamespace(input_source="latest_block", input_chars=120, chunks=1, fallback_reason=None),
                )

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
                select_best_resume_match=select_best_resume_match,
                capture_premium_numbers=lambda *_args, **_kwargs: None,
                record_productivity_event=lambda *_args, **_kwargs: None,
                apply_gmail_label=lambda *_args, **_kwargs: None,
                mark_message_processed=lambda *_args, **_kwargs: None,
                is_recruiter_like=lambda *_args, **_kwargs: True,
                classify_email_intent=lambda **_kwargs: EmailIntentDecision(
                    intent_type="recruiter_job_requirement",
                    action="process_for_queue",
                    confidence=0.92,
                    reason="Matched direct job-description structure.",
                    evidence=["job description"],
                    negative_evidence=[],
                    provider="taxonomy",
                ),
                record_skipped_item=lambda db_ctx, payload: record_skipped_item(db_ctx, payload),
            )

            RunOrchestrator().execute(
                RunOrchestratorRequest(
                    db=db,
                    owner_id="default-owner",
                    items=[self._item("m-ai-2")],
                    user_settings=user_settings,
                    resume=resume,
                    active_resume=resume,
                    enabled_resumes=[resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    run_source="automation_run",
                    run_key="automation_run:test-11",
                    deps=deps,
                )
            )

            self.assertEqual(selection_kwargs["parsed"]["role"], "Merged AI Role")
            self.assertEqual(selection_kwargs["parsed"]["location"], "Dallas, TX")
            self.assertEqual(selection_kwargs["parsed"]["skills_text"], "Java, Amazon ECS")
            self.assertTrue(bool(selection_kwargs["user_settings"].feature_ai_extractor_enabled))
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-ai-2").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.resume_picker_score, 0.83)
            self.assertEqual(row.resume_picker_reason, "Final 0.83; ai=0.90; ats=78.00")
            self.assertEqual(row.resume_picker_candidates_json, '{"rankings":[{"resume_file_name":"resume.pdf","final_resume_score":0.83}]}')
            self.assertEqual(row.resume_picker_breakdown_json, '{"matched_priority_skills":["Java"],"missing_priority_skills":["Oracle"]}')


if __name__ == "__main__":
    unittest.main()
