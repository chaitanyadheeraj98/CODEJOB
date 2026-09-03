import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automation.run_orchestrator import (
    RunOrchestrator,
    RunOrchestratorDependencies,
    RunOrchestratorRequest,
)
from app.db import Base
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision


class HR5DuplicateRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()
        self.owner_id = "default-owner"

        self.user_settings = UserSettings(
            owner_id=self.owner_id,
            enabled=True,
            feature_ai_enabled=False,
            feature_semantic_enabled=False,
        )
        self.db.add(self.user_settings)
        self.resume = ResumeAsset(
            owner_id=self.owner_id,
            file_path="/tmp/resume.pdf",
            file_name="resume.pdf",
            sha256="x" * 64,
            is_current=True,
        )
        self.db.add(self.resume)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_duplicate_external_message_id_is_recovered_without_500(self) -> None:
        external_message_id = "msg-duplicate-1"
        item = {
            "external_message_id": external_message_id,
            "external_thread_id": "thread-1",
            "external_rfc_message_id": "<id-1@example.com>",
            "gmail_received_at": None,
            "sender": "Recruiter <r@example.com>",
            "subject": "Java Developer Role",
            "body": "Please send profile",
            "snippet": "snippet",
            "recipient_email": "to@example.com",
        }

        base_decision = RoutingDecision(
            to_email="to@example.com",
            cc_email="cc@example.com",
            status="safe",
            confidence=0.9,
            reason="resolved",
            evidence=[],
            candidates=[],
            recommended_state="needs_review",
            recommended_skip_reason=None,
            should_mark_failed=False,
            is_sendable_candidate=True,
            needs_manual_confirmation=False,
        )

        def apply_routing_decision(email: RecruiterEmail, routing: RoutingDecision) -> None:
            email.recipient_email = routing.to_email
            email.cc_email = routing.cc_email
            email.routing_status = routing.status
            email.routing_confidence = routing.confidence
            email.routing_reason = routing.reason

        deps = RunOrchestratorDependencies(
            parse_email=lambda _s, _b: {
                "role": "Java Developer",
                "location": "tx",
                "job_location_text": "tx",
                "salary_text": "",
                "skills_text": "java",
                "f2f_mentioned": False,
                "asks_contact_fields": False,
                "is_texas_role": True,
            },
            parse_email_with_details=lambda _s, _b, **_kwargs: (
                {
                    "role": "Java Developer",
                    "location": "tx",
                    "job_location_text": "tx",
                    "salary_text": "",
                    "skills_text": "java",
                    "f2f_mentioned": False,
                    "asks_contact_fields": False,
                    "is_texas_role": True,
                },
                {
                    "parser_version": "base_parser_v1",
                    "source": "gmail",
                    "base_parser_result": {"role": "Java Developer"},
                    "ai_extractor_result": None,
                    "approved_skills_text": "java",
                    "unknown_skills": [],
                    "merged_result": {"role": "Java Developer", "location": "tx", "skills_text": "java"},
                    "ai_merge_notes": [],
                    "source_hints": {},
                },
            ),
            hard_filter_check=lambda *_args, **_kwargs: (True, "hard_filters_passed"),
            compute_blended_ai_score=lambda *_args, **_kwargs: (
                0.91,
                "summary",
                "rules_only",
                None,
                None,
                SimpleNamespace(input_source="latest_block", input_chars=80, chunks=1, fallback_reason=None),
            ),
            policy_f2f_block=lambda _p, _ep, _us: (False, ""),
            evaluate_routing_policy=lambda *_args, **_kwargs: base_decision,
            greeting_from_to_contact=lambda *_args, **_kwargs: "Hi,",
            build_user_fallback_draft=lambda *_args, **_kwargs: "fallback draft",
            generate_reply_with_ai_or_fallback=lambda **_kwargs: None,
            apply_routing_decision=apply_routing_decision,
            select_best_resume_match=lambda **kwargs: SimpleNamespace(
                resume=kwargs.get("fallback_resume"),
                ai_score=0.91,
                ai_summary="summary",
                ai_score_source="rules_only",
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(input_source="latest_block", input_chars=80, chunks=1, fallback_reason=None),
            ),
            capture_premium_numbers=lambda *_args, **_kwargs: None,
            record_productivity_event=lambda *_args, **_kwargs: None,
            apply_gmail_label=lambda *_args, **_kwargs: None,
            mark_message_processed=lambda *_args, **_kwargs: None,
            is_recruiter_like=lambda *_args, **_kwargs: True,
        )

        orchestrator = RunOrchestrator()
        original_commit = self.db.commit
        injected = {"done": False}

        def commit_with_race_insert():
            if not injected["done"]:
                race_db = self.SessionLocal()
                try:
                    exists = (
                        race_db.query(RecruiterEmail)
                        .filter(RecruiterEmail.owner_id == self.owner_id)
                        .filter(RecruiterEmail.external_message_id == external_message_id)
                        .first()
                    )
                    if not exists:
                        race_row = RecruiterEmail(
                            owner_id=self.owner_id,
                            sender="Race <r@example.com>",
                            subject="Race",
                            body="Race body",
                            role="Java Developer",
                            location="tx",
                            salary_text="",
                            skills_text="java",
                            source="gmail",
                            external_message_id=external_message_id,
                        )
                        race_db.add(race_row)
                        race_db.commit()
                finally:
                    race_db.close()
                injected["done"] = True
            return original_commit()

        with patch.object(self.db, "commit", side_effect=commit_with_race_insert):
            result = orchestrator.execute(
                RunOrchestratorRequest(
                    db=self.db,
                    owner_id=self.owner_id,
                    items=[item],
                    user_settings=self.user_settings,
                    resume=self.resume,
                    active_resume=self.resume,
                    enabled_resumes=[self.resume],
                    effective_policy={},
                    threshold=0.6,
                    dry_run=False,
                    model_name="deepseek-chat",
                    deps=deps,
                )
            )

        rows = (
            self.db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == self.owner_id)
            .filter(RecruiterEmail.external_message_id == external_message_id)
            .all()
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(result.matched_count, 1)
        self.assertEqual(result.queued_count, 1)
        self.assertEqual(len(result.queued_email_ids), 1)


if __name__ == "__main__":
    unittest.main()
