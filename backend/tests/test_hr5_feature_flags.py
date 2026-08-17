import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.automation.queue_preparation import QueuePreparationResult
from app.db import Base
from app.models import RecruiterEmail, UserSettings
from app.routing import RoutingDecision
from app.schemas import RegenerateCandidateRequest
from app.services.orchestration_service import OrchestrationService


class HR5FeatureFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.recorded_events: list[dict[str, object]] = []

    def tearDown(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _service(self, *, evaluate_routing_policy, apply_routing_decision) -> OrchestrationService:
        def _record_productivity_event(_db, **kwargs):
            self.recorded_events.append(kwargs)
            return None

        deps = SimpleNamespace(
            owner_id="default-owner",
            evaluate_routing_policy=evaluate_routing_policy,
            apply_routing_decision=apply_routing_decision,
            record_productivity_event=_record_productivity_event,
        )
        return OrchestrationService(deps)

    def test_retry_queue_promotes_sendable_failed_candidate(self) -> None:
        db = self.SessionLocal()
        try:
            email = RecruiterEmail(
                owner_id="default-owner",
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="Body",
                state="failed",
                source="gmail",
                routing_confirmed=False,
                decision="Reject",
                skip_reason="missing_to_or_cc",
                last_error="missing recipients",
            )
            db.add(email)
            db.commit()
            db.refresh(email)

            decision = RoutingDecision(
                to_email="to@example.com",
                cc_email="cc@example.com",
                status="safe",
                confidence=0.95,
                reason="resolved",
                evidence=[],
                candidates=[],
                recommended_state="needs_review",
                recommended_skip_reason=None,
                should_mark_failed=False,
                is_sendable_candidate=True,
                needs_manual_confirmation=False,
            )

            def evaluate_routing_policy(_db, *_args, **_kwargs):
                return decision

            def apply_routing_decision(target_email, routing_decision):
                target_email.recipient_email = routing_decision.to_email
                target_email.cc_email = routing_decision.cc_email
                target_email.routing_status = routing_decision.status
                target_email.routing_confidence = routing_decision.confidence
                target_email.routing_reason = routing_decision.reason

            service = self._service(
                evaluate_routing_policy=evaluate_routing_policy,
                apply_routing_decision=apply_routing_decision,
            )

            def fake_regenerate_candidate(email_id, _payload, target_db):
                target = target_db.get(RecruiterEmail, email_id)
                target.state = "needs_review"
                target.decision = "Qualified"
                target.decision_reason = "Recovered by retry queue routing refresh"
                target.approval_status = "pending"
                target.sent_status = "not_sent"
                target.skip_reason = None
                target.last_error = None
                target.draft_reply = "regenerated draft"
                target_db.commit()
                return target

            service.regenerate_candidate = fake_regenerate_candidate  # type: ignore[method-assign]
            promoted, skipped = service._retry_failed_queue(db)
            db.refresh(email)

            self.assertEqual(promoted, 1)
            self.assertEqual(skipped, 0)
            self.assertEqual(email.state, "needs_review")
            self.assertEqual(email.decision, "Qualified")
            self.assertIsNone(email.skip_reason)
            self.assertIsNone(email.last_error)
            self.assertEqual(email.recipient_email, "to@example.com")
            self.assertEqual(email.cc_email, "cc@example.com")
            self.assertEqual(email.draft_reply, "regenerated draft")
            self.assertTrue(any(evt.get("event_type") == "needs_review_marked" for evt in self.recorded_events))
        finally:
            db.close()

    def test_retry_queue_skips_item_when_regenerate_fails_and_continues_batch(self) -> None:
        db = self.SessionLocal()
        try:
            failing_email = RecruiterEmail(
                owner_id="default-owner",
                sender="Recruiter <r1@example.com>",
                subject="Role 1",
                body="Body 1",
                state="failed",
                source="gmail",
                routing_confirmed=False,
                decision="Reject",
            )
            ok_email = RecruiterEmail(
                owner_id="default-owner",
                sender="Recruiter <r2@example.com>",
                subject="Role 2",
                body="Body 2",
                state="failed",
                source="gmail",
                routing_confirmed=False,
                decision="Reject",
            )
            db.add_all([failing_email, ok_email])
            db.commit()
            db.refresh(failing_email)
            db.refresh(ok_email)

            decision = RoutingDecision(
                to_email="to@example.com",
                cc_email="cc@example.com",
                status="safe",
                confidence=0.95,
                reason="resolved",
                evidence=[],
                candidates=[],
                recommended_state="needs_review",
                recommended_skip_reason=None,
                should_mark_failed=False,
                is_sendable_candidate=True,
                needs_manual_confirmation=False,
            )

            def evaluate_routing_policy(_db, *_args, **_kwargs):
                return decision

            def apply_routing_decision(target_email, routing_decision):
                target_email.recipient_email = routing_decision.to_email
                target_email.cc_email = routing_decision.cc_email
                target_email.routing_status = routing_decision.status

            service = self._service(
                evaluate_routing_policy=evaluate_routing_policy,
                apply_routing_decision=apply_routing_decision,
            )

            def fake_regenerate_candidate(email_id, _payload, target_db):
                if email_id == failing_email.id:
                    raise RuntimeError("boom")
                target = target_db.get(RecruiterEmail, email_id)
                target.state = "needs_review"
                target.draft_reply = "regenerated draft"
                target_db.commit()
                return target

            service.regenerate_candidate = fake_regenerate_candidate  # type: ignore[method-assign]
            promoted, skipped = service._retry_failed_queue(db)
            db.refresh(failing_email)
            db.refresh(ok_email)

            self.assertEqual(promoted, 1)
            self.assertEqual(skipped, 1)
            self.assertEqual(failing_email.state, "failed")
            self.assertEqual(ok_email.state, "needs_review")
            self.assertEqual(ok_email.draft_reply, "regenerated draft")
        finally:
            db.close()

    def test_regenerate_candidate_syncs_stale_qualification_fields(self) -> None:
        db = self.SessionLocal()
        try:
            user_settings = UserSettings(owner_id="default-owner")
            db.add(user_settings)
            email = RecruiterEmail(
                owner_id="default-owner",
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="Body",
                state="needs_review",
                source="gmail",
                routing_confirmed=False,
                decision="Qualified",
                qualification_result="rejected",
                blocking_rule="recipient_mapping",
                qualification_detail="Recipient routing could not resolve both recruiter To and employer CC.",
                draft_reply="",
            )
            db.add(email)
            db.commit()
            db.refresh(email)
            db.refresh(user_settings)

            deps = SimpleNamespace(
                owner_id="default-owner",
                is_terminal_state=lambda _email: False,
                get_settings=lambda _db: user_settings,
                effective_run_inputs=lambda _settings, _override: SimpleNamespace(policy={}),
                policy_threshold=lambda _settings, _policy: 0.6,
                active_resume=lambda _db: None,
                enabled_resumes=lambda _db: [],
                select_best_resume_match=lambda **_kwargs: SimpleNamespace(),
                parse_email_with_details=lambda _subject, _body, **_kwargs: (
                    {"role": "Test Role", "location": "", "salary_text": "", "skills_text": ""},
                    {},
                ),
                apply_routing_decision=lambda *_args, **_kwargs: None,
                record_productivity_event=lambda _db, **_kwargs: None,
                model_name="test-model",
                parse_email=lambda *_args, **_kwargs: None,
                hard_filter_check=lambda *_args, **_kwargs: None,
                compute_blended_ai_score=lambda *_args, **_kwargs: None,
                policy_f2f_block=lambda *_args, **_kwargs: None,
                evaluate_routing_policy=lambda *_args, **_kwargs: None,
                greeting_from_to_contact=lambda *_args, **_kwargs: None,
                build_user_fallback_draft=lambda *_args, **_kwargs: None,
                generate_reply_with_ai_or_fallback=lambda *_args, **_kwargs: None,
            )
            service = OrchestrationService(deps)

            fresh_preparation = QueuePreparationResult(
                outcome="needs_review",
                parsed={"role": "Test Role", "location": "", "salary_text": "", "skills_text": ""},
                hard_filter_reason="",
                ai_score=0.7,
                ai_summary="",
                ai_score_source="test",
                email_embedding_json=None,
                resume_embedding_json=None,
                semantic_diag=SimpleNamespace(input_source=None, input_chars=None, chunks=None, fallback_reason=None, keyword_source=None, thread_snapshot_used=None, thread_snapshot_email_id=None),
                routing_decision=None,
                decision_reason="Qualified and queued for manual approval",
                auto_reject_reason=None,
                skip_reason=None,
                qualification_result="qualified",
                blocking_rule=None,
                qualification_detail="Qualified for queue review.",
                qualification_context={"warnings": []},
                draft_reply="Fresh regenerated draft body.",
                draft_source="rules_only",
                draft_model=None,
                draft_ai_error=None,
                draft_resume_context_status=None,
            )

            with patch(
                "app.services.orchestration_service.prepare_candidate_for_queue",
                return_value=fresh_preparation,
            ):
                result = service.regenerate_candidate(email.id, RegenerateCandidateRequest(), db)

            self.assertEqual(result.qualification_result, "qualified")
            self.assertIsNone(result.blocking_rule)
            self.assertEqual(result.qualification_detail, "Qualified for queue review.")
            self.assertEqual(result.draft_reply, "Fresh regenerated draft body.")
            self.assertEqual(result.state, "needs_review")
        finally:
            db.close()

    def test_auto_send_executor_counts_failures_and_continues(self) -> None:
        service = self._service(
            evaluate_routing_policy=lambda *_args, **_kwargs: None,
            apply_routing_decision=lambda *_args, **_kwargs: None,
        )

        calls: list[int] = []

        def fake_approve_send(email_id, _payload, _db):
            calls.append(email_id)
            if email_id == 2:
                raise HTTPException(status_code=400, detail="blocked")
            return None

        service.approve_send = fake_approve_send  # type: ignore[method-assign]

        sent, failed = service._auto_send_newly_queued([1, 2, 3], db=None)

        self.assertEqual(calls, [1, 2, 3])
        self.assertEqual(sent, 2)
        self.assertEqual(failed, 1)
        self.assertTrue(any(evt.get("event_type") == "auto_send_failed" for evt in self.recorded_events))


if __name__ == "__main__":
    unittest.main()
