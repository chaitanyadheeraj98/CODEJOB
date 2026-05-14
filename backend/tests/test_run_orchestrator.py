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

    def _seed_user_settings(self, db: Session, *, feature_ai_enabled: bool) -> UserSettings:
        user_settings = UserSettings(
            owner_id="default-owner",
            enabled=True,
            gmail_query="is:unread",
            default_gmail_query="is:unread",
            default_date_mode="off",
            qualification_threshold=0.6,
            feature_ai_enabled=feature_ai_enabled,
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
                "salary_text": "$60/hr",
                "skills_text": "java",
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
        ) -> tuple[float, str, str, str | None, str | None]:
            return ai_score, "summary", "v1", None, None

        def policy_f2f_block(
            _parsed: dict[str, str | int | bool], _policy: dict[str, object]
        ) -> tuple[bool, str]:
            return (True, "blocked") if blocked else (False, "")

        def analyze(
            _db: Session, _sender: str, _subject: str, _body: str, _snippet: str
        ) -> RoutingResult:
            evidence = []
            if to_email:
                evidence.append(RoutingEvidence(role="to", email=to_email, source="test", detail="to"))
            if cc_email:
                evidence.append(RoutingEvidence(role="cc", email=cc_email, source="test", detail="cc"))
            return RoutingResult(
                to_email=to_email,
                cc_email=cc_email,
                status="safe" if to_email and cc_email else "missing",
                confidence=0.9 if to_email and cc_email else 0.0,
                reason="test",
                evidence=evidence,
                candidates=evidence,
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

        def apply_routing_result(email: RecruiterEmail, routing: RoutingResult) -> None:
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

        deps = RunOrchestratorDependencies(
            parse_email=parse_email,
            hard_filter_check=hard_filter_check,
            compute_blended_ai_score=compute_blended,
            policy_f2f_block=policy_f2f_block,
            analyze_email_routing=analyze,
            greeting_from_to_contact=lambda _to, _body: "Hi Recruiter,",
            build_user_fallback_draft=build_user_fallback_draft,
            generate_reply_with_ai_or_fallback=lambda **_kwargs: SimpleNamespace(
                draft_text="ai draft",
                source="deepseek",
                ai_model="deepseek-chat",
                ai_error=None,
                resume_context_status="injected",
            ),
            apply_routing_result=apply_routing_result,
            record_productivity_event=record_productivity_event,
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


if __name__ == "__main__":
    unittest.main()
