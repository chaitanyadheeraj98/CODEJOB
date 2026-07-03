import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.gates import EmailIntentDecision
from app.db import Base
from app.models import RecruiterEmail, SyncRun, UserSettings
from app.recent_runs import RUN_SOURCE_GMAIL_SYNC, gmail_sync_run_key
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService


class OrchestrationServiceIntentGateTests(unittest.TestCase):
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

    def test_sync_gmail_processes_real_job_even_without_recruiter_words(self) -> None:
        with Session(self.engine) as db:
            user_settings = UserSettings(
                owner_id="default-owner",
                enabled=True,
                gmail_query="is:unread",
                default_gmail_query="is:unread",
                default_date_mode="off",
                qualification_threshold=0.6,
                feature_ai_enabled=False,
                feature_ai_extractor_enabled=False,
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

            deps = OrchestrationDeps(
                owner_id="default-owner",
                model_name="deepseek-chat",
                get_settings=lambda _db: user_settings,
                active_resume=lambda _db: None,
                enabled_resumes=lambda _db: [],
                enabled_attachment_assets=lambda _db: [],
                effective_run_inputs=lambda *_args, **_kwargs: SimpleNamespace(effective_query="is:unread", policy={}),
                compute_blended_ai_score=lambda **_kwargs: (0.9, "summary", "test", None, None, SimpleNamespace()),
                select_best_resume_match=lambda **_kwargs: SimpleNamespace(
                    resume=None,
                    ai_score=0.9,
                    ai_summary="summary",
                    ai_score_source="test",
                    ats_score=None,
                    ats_summary=None,
                    ats_score_source=None,
                    ats_breakdown_json=None,
                    email_embedding_json=None,
                    resume_embedding_json=None,
                    semantic_diag=SimpleNamespace(),
                ),
                analyze_email_routing=lambda *_args, **_kwargs: SimpleNamespace(to_email="to@example.com", cc_email="cc@example.com"),
                build_user_fallback_draft=lambda *_args, **_kwargs: "fallback",
                apply_routing_result=lambda email, routed: (
                    setattr(email, "recipient_email", routed.to_email),
                    setattr(email, "cc_email", routed.cc_email),
                ),
                apply_gmail_label_for_email=lambda **_kwargs: None,
                log_gmail_labeling_stats=lambda: None,
                build_run_response=lambda *args, **kwargs: None,
                record_productivity_event=lambda *args, **kwargs: None,
                policy_threshold=lambda *_args, **_kwargs: 0.6,
                policy_batch_limit=lambda *_args, **_kwargs: 20,
                policy_dry_run=lambda *_args, **_kwargs: False,
                policy_f2f_block=lambda *_args, **_kwargs: (False, ""),
                evaluate_routing_policy=lambda *_args, **_kwargs: None,
                apply_routing_decision=lambda *_args, **_kwargs: None,
                capture_premium_numbers=lambda *_args, **_kwargs: None,
                percentile_ms=lambda *_args, **_kwargs: 0.0,
                begin_embedding_latency_capture=lambda: None,
                end_embedding_latency_capture=lambda: [],
                embedding_latency_log_enabled=lambda: False,
                embedding_provider=lambda: "sbert",
                embedding_model=lambda: "mini",
                evaluate_routing_for_email=lambda *_args, **_kwargs: None,
                is_terminal_state=lambda *_args, **_kwargs: False,
                email_domain=lambda value: value.split("@")[-1],
                telegram_notify=lambda _msg: None,
                build_telegram_digest=lambda *_args, **_kwargs: "",
                set_last_gmail_sync_at=lambda _ts: None,
                set_ai_runtime=lambda _vals: None,
                is_gmail_configured=lambda: True,
                gmail_auth_status=lambda: (True, True, ""),
                oauth_bootstrap_status=lambda: (False, None),
                list_unread_candidates_by_query=lambda *_args, **_kwargs: [
                    {
                        "sender": "jobs@googlegroups.com",
                        "subject": "Java Developer - Texas",
                        "body": (
                            "Position: Java Developer\n"
                            "Required Skills: Java, Spring Boot\n"
                            "Location: Texas\n"
                            "Duration: 12 months\n"
                            "Share resume."
                        ),
                        "snippet": "Position: Java Developer",
                        "external_message_id": "m-sync-1",
                        "external_thread_id": "t-sync-1",
                        "external_rfc_message_id": "rfc-sync-1",
                        "gmail_received_at": datetime.now(UTC),
                        "recipient_email": "legacy-to@example.com",
                    }
                ],
                is_recruiter_like=lambda *_args, **_kwargs: False,
                classify_email_intent=lambda **_kwargs: EmailIntentDecision(
                    intent_type="recruiter_job_requirement",
                    action="process_for_queue",
                    confidence=0.93,
                    reason="Matched direct job-description structure.",
                    evidence=["position", "required skills", "location"],
                    negative_evidence=[],
                    provider="taxonomy",
                ),
                parse_email=lambda *_args, **_kwargs: {},
                parse_email_with_details=lambda *_args, **_kwargs: (
                    {
                        "role": "Java Developer",
                        "location": "Texas",
                        "salary_text": "$60/hr",
                        "skills_text": "Java, Spring Boot",
                    },
                    {"parser_version": "base_only_v2"},
                ),
                hard_filter_check=lambda *_args, **_kwargs: (True, "hard_filters_passed"),
                greeting_from_to_contact=lambda *_args, **_kwargs: "Hi Recruiter,",
                generate_reply_with_ai_or_fallback=lambda **_kwargs: None,
                send_reply_with_attachment=lambda *_args, **_kwargs: "",
                send_new_email_with_attachment=lambda *_args, **_kwargs: "",
                mark_message_processed=lambda *_args, **_kwargs: None,
                append_tracking_sheet_row=lambda **_kwargs: None,
            )

            response = OrchestrationService(deps).sync_gmail(db)

            self.assertEqual(response.imported_count, 1)
            self.assertEqual(response.skipped_count, 0)
            row = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-sync-1").first()
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.intent_type, "recruiter_job_requirement")
            self.assertEqual(row.gate_provider, "taxonomy")


if __name__ == "__main__":
    unittest.main()
