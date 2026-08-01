import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.gates import EmailIntentDecision
from app.db import Base
from app.models import RecruiterEmail, UserSettings
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService
from app.services.role_manifest_service import RoleManifestService


TWO_ROLE_SOURCE = "Platform Engineer role details here.\nData Engineer role details here."


def _two_role_manifest():
    return RoleManifestService(
        provider=lambda system, user: {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.96,
            "shared_constraints": [],
            "roles": [
                {"index": 1, "title_hint": "Platform Engineer", "start_line": 1, "end_line": 1, "confidence": 0.98},
                {"index": 2, "title_hint": "Data Engineer", "start_line": 2, "end_line": 2, "confidence": 0.98},
            ],
        }
    ).detect(TWO_ROLE_SOURCE)


class SyncGmailRoleManifestOrderingTests(unittest.TestCase):
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

    def _deps(self, *, user_settings: UserSettings, parse_calls: list[dict[str, object]], regenerate_calls: list[int]) -> OrchestrationDeps:
        def parse_email_with_details(subject, body, *, source="gmail", ai_extractor_enabled=False, **_kwargs):
            parse_calls.append({"subject": subject, "body": body, "ai_extractor_enabled": ai_extractor_enabled})
            role = "Platform Engineer" if "Platform Engineer" in body else "Data Engineer" if "Data Engineer" in body else "Engineer"
            return (
                {"role": role, "location": "Remote", "salary_text": "not_specified", "skills_text": "Python"},
                {"parser_version": "base_only_v2"},
            )

        return OrchestrationDeps(
            owner_id="default-owner",
            model_name="deepseek-chat",
            get_settings=lambda _db: user_settings,
            active_resume=lambda _db: None,
            enabled_resumes=lambda _db: [],
            enabled_attachment_assets=lambda _db: [],
            effective_run_inputs=lambda *_a, **_k: SimpleNamespace(effective_query="is:unread", policy={}),
            compute_blended_ai_score=lambda **_k: (0.9, "summary", "test", None, None, SimpleNamespace()),
            select_best_resume_match=lambda **_k: SimpleNamespace(
                resume=None, ai_score=0.9, ai_summary="summary", ai_score_source="test",
                ats_score=None, ats_summary=None, ats_score_source=None, ats_breakdown_json=None,
                email_embedding_json=None, resume_embedding_json=None, semantic_diag=SimpleNamespace(),
            ),
            analyze_email_routing=lambda *_a, **_k: SimpleNamespace(to_email="to@example.com", cc_email="cc@example.com"),
            build_user_fallback_draft=lambda *_a, **_k: "fallback",
            apply_routing_result=lambda email, routed: (
                setattr(email, "recipient_email", routed.to_email),
                setattr(email, "cc_email", routed.cc_email),
            ),
            apply_gmail_label_for_email=lambda **_k: None,
            log_gmail_labeling_stats=lambda: None,
            build_run_response=lambda *a, **k: None,
            record_productivity_event=lambda *a, **k: None,
            policy_threshold=lambda *_a, **_k: 0.6,
            policy_batch_limit=lambda *_a, **_k: 20,
            policy_dry_run=lambda *_a, **_k: False,
            policy_f2f_block=lambda *_a, **_k: (False, ""),
            evaluate_routing_policy=lambda *_a, **_k: None,
            apply_routing_decision=lambda *_a, **_k: None,
            capture_premium_numbers=lambda *_a, **_k: None,
            percentile_ms=lambda *_a, **_k: 0.0,
            begin_embedding_latency_capture=lambda: None,
            end_embedding_latency_capture=lambda: [],
            embedding_latency_log_enabled=lambda: False,
            embedding_provider=lambda: "sbert",
            embedding_model=lambda: "mini",
            evaluate_routing_for_email=lambda *_a, **_k: None,
            is_terminal_state=lambda *_a, **_k: False,
            email_domain=lambda value: value.split("@")[-1],
            telegram_notify=lambda _msg: None,
            build_telegram_digest=lambda *_a, **_k: "",
            set_last_gmail_sync_at=lambda _ts: None,
            set_ai_runtime=lambda _v: None,
            is_gmail_configured=lambda: True,
            gmail_auth_status=lambda: (True, True, ""),
            oauth_bootstrap_status=lambda: (False, None),
            list_unread_candidates_by_query=lambda *_a, **_k: [
                {
                    "sender": "jobs@example.com",
                    "subject": "Two roles in one email",
                    "body": TWO_ROLE_SOURCE,
                    "snippet": "Two roles",
                    "external_message_id": "m-multi-1",
                    "external_thread_id": "t-multi-1",
                    "external_rfc_message_id": "rfc-multi-1",
                    "gmail_received_at": datetime.now(UTC),
                    "recipient_email": "legacy-to@example.com",
                    "list_post": "",
                    "list_unsubscribe": "",
                    "list_id": "",
                    "to_header": "",
                    "cc_header": "",
                    "delivered_to": "",
                    "mailing_list": "",
                }
            ],
            is_recruiter_like=lambda *_a, **_k: True,
            classify_email_intent=lambda **kwargs: EmailIntentDecision(
                intent_type="recruiter_job_requirement",
                action="process_for_queue",
                confidence=0.93,
                reason="Matched direct job-description structure.",
                evidence=["position"],
                negative_evidence=[],
                provider="taxonomy",
            ),
            parse_email=lambda *_a, **_k: {},
            parse_email_with_details=parse_email_with_details,
            hard_filter_check=lambda *_a, **_k: (True, "hard_filters_passed"),
            greeting_from_to_contact=lambda *_a, **_k: "Hi Recruiter,",
            generate_reply_with_ai_or_fallback=lambda **_k: None,
            send_reply_with_attachment=lambda *_a, **_k: "",
            send_new_email_with_attachment=lambda *_a, **_k: "",
            mark_message_processed=lambda *_a, **_k: None,
            append_tracking_sheet_row=lambda **_k: None,
        )

    def test_multi_role_email_skips_full_body_ai_extractor_and_creates_children(self) -> None:
        with Session(self.engine) as db:
            user_settings = UserSettings(
                owner_id="default-owner",
                enabled=True,
                gmail_query="is:unread",
                default_gmail_query="is:unread",
                default_date_mode="off",
                qualification_threshold=0.6,
                feature_ai_enabled=False,
                feature_ai_extractor_enabled=True,
                feature_role_manifest_enabled=True,
                feature_semantic_enabled=False,
                feature_groq_job_parser_enabled=False,
                feature_gmail_requirement_groups_enabled=False,
                fallback_draft_template="Hi",
                signature_name="Tester",
                signature_phone="+1",
                signature_email="tester@example.com",
                policy_json="",
            )
            db.add(user_settings)
            db.commit()

            parse_calls: list[dict[str, object]] = []
            regenerate_calls: list[int] = []
            deps = self._deps(user_settings=user_settings, parse_calls=parse_calls, regenerate_calls=regenerate_calls)
            service = OrchestrationService(deps)

            def fake_regenerate_candidate(email_id, _payload, target_db):
                regenerate_calls.append(email_id)
                child = target_db.get(RecruiterEmail, email_id)
                child.draft_reply = "child draft"
                child.state = "needs_review"
                target_db.commit()
                return child

            service.regenerate_candidate = fake_regenerate_candidate  # type: ignore[method-assign]

            with patch(
                "app.services.orchestration_service.RoleManifestService",
                return_value=SimpleNamespace(detect=lambda body: _two_role_manifest()),
            ):
                response = service.sync_gmail(db)

            self.assertEqual(response.imported_count, 1)

            parent = db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "m-multi-1").first()
            self.assertIsNotNone(parent)
            self.assertEqual(parent.role_manifest_status, "multiple")
            self.assertTrue(parent.is_source_parent)

            # The parent's own parse call must not have attempted the (truncation-prone)
            # full-body AI extractor once we already know the email is multi-role.
            parent_calls = [c for c in parse_calls if c["body"] == TWO_ROLE_SOURCE]
            self.assertEqual(len(parent_calls), 1)
            self.assertFalse(parent_calls[0]["ai_extractor_enabled"])

            children = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.source_parent_email_id == parent.id)
                .order_by(RecruiterEmail.requirement_index)
                .all()
            )
            self.assertEqual(len(children), 2)
            self.assertEqual({c.role for c in children}, {"Platform Engineer", "Data Engineer"})
            # Each child got its own bounded-text parse call, with the AI extractor enabled.
            child_calls = [c for c in parse_calls if c["body"] != TWO_ROLE_SOURCE]
            self.assertEqual(len(child_calls), 2)
            self.assertTrue(all(c["ai_extractor_enabled"] for c in child_calls))
            self.assertEqual(sorted(regenerate_calls), sorted(child.id for child in children))
            self.assertTrue(all((child.draft_reply or "") == "child draft" for child in children))


if __name__ == "__main__":
    unittest.main()
