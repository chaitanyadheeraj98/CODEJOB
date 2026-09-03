import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.gates import EmailIntentDecision
from app.db import Base
from app.main import _build_run_response
from app.models import RecentRunSkippedItem, ResumeAsset, UserSettings
from app.phase0 import email_domain
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService


def _make_deps(user_settings: UserSettings, classify_email_intent, list_unread_candidates_by_query) -> OrchestrationDeps:
    return OrchestrationDeps(
        owner_id="default-owner",
        model_name="deepseek-chat",
        get_settings=lambda _db: user_settings,
        active_resume=lambda db: db.query(ResumeAsset).filter(ResumeAsset.owner_id == "default-owner").first(),
        enabled_resumes=lambda db: db.query(ResumeAsset).filter(ResumeAsset.owner_id == "default-owner").all(),
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
        apply_routing_result=lambda email, routed: None,
        apply_gmail_label_for_email=lambda **_kwargs: None,
        log_gmail_labeling_stats=lambda: None,
        build_run_response=_build_run_response,
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
        email_domain=email_domain,
        telegram_notify=lambda _msg: None,
        build_telegram_digest=lambda *_args, **_kwargs: "",
        set_last_gmail_sync_at=lambda _ts: None,
        set_ai_runtime=lambda _vals: None,
        is_gmail_configured=lambda: True,
        gmail_auth_status=lambda: (True, True, ""),
        oauth_bootstrap_status=lambda: (False, None),
        list_unread_candidates_by_query=list_unread_candidates_by_query,
        is_recruiter_like=lambda *_args, **_kwargs: False,
        classify_email_intent=classify_email_intent,
        parse_email=lambda *_args, **_kwargs: {},
        parse_email_with_details=lambda *_args, **_kwargs: ({}, {"parser_version": "base_only_v2"}),
        hard_filter_check=lambda *_args, **_kwargs: (True, "hard_filters_passed"),
        greeting_from_to_contact=lambda *_args, **_kwargs: "Hi Recruiter,",
        generate_reply_with_ai_or_fallback=lambda **_kwargs: None,
        send_reply_with_attachment=lambda *_args, **_kwargs: "",
        send_new_email_with_attachment=lambda *_args, **_kwargs: "",
        mark_message_processed=lambda *_args, **_kwargs: None,
        append_tracking_sheet_row=lambda **_kwargs: None,
    )


class OrchestrationServiceRunOnceRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _user_settings(self) -> UserSettings:
        return UserSettings(
            owner_id="default-owner",
            enabled=True,
            gmail_query="is:unread",
            default_gmail_query="is:unread",
            default_date_mode="off",
            qualification_threshold=0.6,
            fallback_draft_template="Hi",
            signature_name="Tester",
            signature_phone="+1",
            signature_email="tester@example.com",
            policy_json="",
        )

    def test_items_override_bypasses_live_query_and_reprocesses_only_selected_message(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._user_settings()
            db.add(user_settings)
            db.add(ResumeAsset(owner_id="default-owner", file_path="/tmp/r.pdf", file_name="r.pdf", sha256="x" * 64, is_current=True))
            db.commit()

            classify_email_intent = Mock(
                return_value=EmailIntentDecision(
                    intent_type="not_a_recruiter_email",
                    action="skip",
                    confidence=0.4,
                    reason="Retried item still reads as spam.",
                    evidence=[],
                    negative_evidence=[],
                    provider="taxonomy",
                )
            )
            list_unread_candidates_by_query = Mock(side_effect=AssertionError("live query must not run when items_override is given"))
            deps = _make_deps(user_settings, classify_email_intent, list_unread_candidates_by_query)

            item = {
                "sender": "Recruiter <r@example.com>",
                "subject": "Java Developer - Texas",
                "body": "Position: Java Developer",
                "snippet": "Position: Java Developer",
                "external_message_id": "m-retry-selected",
                "external_thread_id": "t-retry-selected",
                "external_rfc_message_id": "rfc-retry-selected",
                "gmail_received_at": datetime.now(UTC),
                "recipient_email": "legacy-to@example.com",
            }

            response = OrchestrationService(deps).run_once(None, db, items_override=[item])

            list_unread_candidates_by_query.assert_not_called()
            self.assertEqual(response.matched_count, 1)
            self.assertEqual(response.queued_count, 0)
            self.assertEqual(response.skipped_count, 1)
            self.assertIn("selected emails", response.detail)
            self.assertNotIn("unread matching emails", response.detail)
            skipped = (
                db.query(RecentRunSkippedItem)
                .filter(RecentRunSkippedItem.external_message_id == "m-retry-selected")
                .one()
            )
            self.assertEqual(skipped.run_key, response.run_key)

    def test_items_override_empty_reports_idle_without_touching_live_query(self) -> None:
        with Session(self.engine) as db:
            user_settings = self._user_settings()
            db.add(user_settings)
            db.add(ResumeAsset(owner_id="default-owner", file_path="/tmp/r.pdf", file_name="r.pdf", sha256="y" * 64, is_current=True))
            db.commit()

            list_unread_candidates_by_query = Mock(side_effect=AssertionError("live query must not run when items_override is given"))
            deps = _make_deps(user_settings, Mock(), list_unread_candidates_by_query)

            response = OrchestrationService(deps).run_once(None, db, items_override=[])

            list_unread_candidates_by_query.assert_not_called()
            self.assertEqual(response.status, "idle")
            self.assertIn("selected messages", response.detail)


if __name__ == "__main__":
    unittest.main()
