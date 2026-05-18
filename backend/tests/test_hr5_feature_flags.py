import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import RecruiterEmail
from app.routing import RoutingDecision
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
            self.assertTrue(any(evt.get("event_type") == "needs_review_marked" for evt in self.recorded_events))
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
