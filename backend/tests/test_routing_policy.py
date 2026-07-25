import unittest

from app.phase0 import RoutingResult, analyze_recipient_routing
from app.models import RecruiterEmail
from app.routing import (
    HeuristicRoutingAdapter,
    RoutingPolicyInput,
    RoutingPolicyService,
)
from app.services.routing_runtime_service import RoutingRuntimeDeps, RoutingRuntimeService


EMAIL_30_BODY = """
Title: Java Microservices RPA Developer

Location: Plano, TX

Thanks
Sudarsan
Email: sudarsan@cystemslogic.com

Thanks & Regards
Prashanth Kinnera
Email: kprashanth@horizonsoftech.net
"""


class RoutingPolicyTests(unittest.TestCase):
    def test_safe_distinct_addresses_are_sendable_and_queueable(self) -> None:
        service = RoutingPolicyService(adapter=HeuristicRoutingAdapter())
        decision = service.evaluate(
            RoutingPolicyInput(
                sender="Prashanth Kinnera <kprashanth@horizonsoftech.net>",
                subject="Java Microservices RPA Developer",
                body=EMAIL_30_BODY,
            )
        )
        self.assertEqual(decision.recommended_state, "needs_review")
        self.assertTrue(decision.is_sendable_candidate)
        self.assertFalse(decision.should_mark_failed)
        self.assertFalse(decision.needs_manual_confirmation)

    def test_missing_addresses_recommend_failed_with_skip_reason(self) -> None:
        service = RoutingPolicyService(adapter=HeuristicRoutingAdapter())
        decision = service.evaluate(
            RoutingPolicyInput(
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="No emails in body",
            )
        )
        self.assertTrue(decision.should_mark_failed)
        self.assertEqual(decision.recommended_state, "failed")
        self.assertEqual(decision.recommended_skip_reason, "missing_to_or_cc")

    def test_confirmed_override_marks_sendable_even_if_low_confidence(self) -> None:
        class LowConfidenceAdapter:
            def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
                _ = payload
                return RoutingResult(
                    to_email="to@example.com",
                    cc_email="cc@example.com",
                    status="ambiguous",
                    confidence=0.2,
                    reason="test",
                    evidence=[],
                    candidates=[],
                )

        service = RoutingPolicyService(adapter=LowConfidenceAdapter())
        decision = service.evaluate(
            RoutingPolicyInput(
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="Body",
                routing_confirmed=True,
            )
        )
        self.assertTrue(decision.is_sendable_candidate)

    def test_identical_to_cc_is_downgraded_to_ambiguous_and_not_sendable(self) -> None:
        class DuplicatePairAdapter:
            def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
                _ = payload
                return RoutingResult(
                    to_email="hr@horizonsoftech.net",
                    cc_email="hr@horizonsoftech.net",
                    status="safe",
                    confidence=0.9,
                    reason="Found distinct recruiter and employer contacts in the current email.",
                    evidence=[],
                    candidates=[],
                )

        service = RoutingPolicyService(adapter=DuplicatePairAdapter())
        decision = service.evaluate(
            RoutingPolicyInput(
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="Body",
            )
        )
        self.assertEqual(decision.status, "ambiguous")
        self.assertFalse(decision.is_sendable_candidate)
        self.assertTrue(decision.needs_manual_confirmation)
        self.assertFalse(decision.should_mark_failed)
        self.assertEqual(decision.recommended_state, "needs_review")
        self.assertEqual(decision.reason, "Recruiter To and employer CC resolved to the same address.")

    def test_identical_to_cc_normalization_handles_case_and_whitespace(self) -> None:
        class DuplicatePairFormattingAdapter:
            def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
                _ = payload
                return RoutingResult(
                    to_email=" HR@HorizonSoftech.net ",
                    cc_email="hr@horizonsoftech.net",
                    status="confirmed",
                    confidence=0.92,
                    reason="Matched a prior correction and both addresses appear in this email.",
                    evidence=[],
                    candidates=[],
                )

        service = RoutingPolicyService(adapter=DuplicatePairFormattingAdapter())
        decision = service.evaluate(
            RoutingPolicyInput(
                sender="Recruiter <r@example.com>",
                subject="Role",
                body="Body",
            )
        )
        self.assertEqual(decision.status, "ambiguous")
        self.assertFalse(decision.is_sendable_candidate)
        self.assertTrue(decision.needs_manual_confirmation)

    def test_heuristic_adapter_parity_with_phase0(self) -> None:
        payload = RoutingPolicyInput(
            sender="Prashanth Kinnera <kprashanth@horizonsoftech.net>",
            subject="Java Microservices RPA Developer",
            body=EMAIL_30_BODY,
        )
        expected = analyze_recipient_routing(payload.sender, payload.subject, payload.body, payload.snippet)
        actual = HeuristicRoutingAdapter().evaluate(payload)
        self.assertEqual(actual.to_email, expected.to_email)
        self.assertEqual(actual.cc_email, expected.cc_email)
        self.assertEqual(actual.status, expected.status)
        self.assertEqual(actual.confidence, expected.confidence)

    def test_routing_decision_assignment_matches_routing_result_assignment(self) -> None:
        service = RoutingRuntimeService(
            RoutingRuntimeDeps(owner_id="default-owner", get_employer_domains=lambda db: [])
        )
        decision = RoutingPolicyService(
            adapter=HeuristicRoutingAdapter()
        ).evaluate(
            RoutingPolicyInput(
                sender="Prashanth Kinnera <kprashanth@horizonsoftech.net>",
                subject="Java Microservices RPA Developer",
                body=EMAIL_30_BODY,
            )
        )
        from_decision = RecruiterEmail(
            owner_id="default-owner",
            sender="recruiter@example.com",
            subject="Role",
            body="Body",
            state="needs_review",
            decision="Qualified",
            source="gmail",
        )
        from_result = RecruiterEmail(
            owner_id="default-owner",
            sender="recruiter@example.com",
            subject="Role",
            body="Body",
            state="needs_review",
            decision="Qualified",
            source="gmail",
        )

        service.apply_routing_decision(from_decision, decision)
        service.apply_routing_result(from_result, decision.to_routing_result())

        fields = (
            "recipient_email",
            "cc_email",
            "routing_status",
            "routing_confidence",
            "routing_reason",
            "routing_evidence",
            "routing_candidates",
        )
        self.assertEqual(
            tuple(getattr(from_decision, field) for field in fields),
            tuple(getattr(from_result, field) for field in fields),
        )


if __name__ == "__main__":
    unittest.main()
