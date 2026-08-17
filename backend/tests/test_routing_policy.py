import unittest

from app.models import RecruiterEmail
from app.phase0 import RoutingEvidence, analyze_recipient_routing, extract_recipient_routing_candidates
from app.routing import CcSelectionRequest, RoutingPolicyInput, RoutingPolicyService
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


def evidence(role: str, email: str, source: str = "test") -> RoutingEvidence:
    return RoutingEvidence(role=role, email=email, source=source, detail="test")


class RoutingPolicyTests(unittest.TestCase):
    def evaluate(
        self,
        *,
        to: list[RoutingEvidence] | None = None,
        cc: list[RoutingEvidence] | None = None,
        preferred: list[str] | None = None,
        default: list[str] | None = None,
    ):
        return RoutingPolicyService().evaluate(
            CcSelectionRequest(
                to_candidates=to or [],
                cc_candidates=cc or [],
                preferred_employer_cc_emails=preferred or [],
                default_employer_cc_emails=default or [],
            )
        )

    def test_single_sender_and_recruiter_equal_sender_are_valid(self) -> None:
        decision = self.evaluate(
            to=[evidence("to", "recruiter@example.com", "sender_header")],
            cc=[
                evidence("cc", "RECRUITER@example.com"),
                evidence("cc", "employer@example.com"),
            ],
        )

        self.assertEqual(decision.to_email, "recruiter@example.com")
        self.assertEqual(decision.cc_email, "employer@example.com")
        self.assertTrue(decision.is_sendable_candidate)
        self.assertFalse(decision.needs_manual_confirmation)

    def test_one_distinct_to_wins_but_multiple_distinct_to_candidates_fail(self) -> None:
        sender = evidence("to", "sender@example.com", "sender_header")
        selected = self.evaluate(
            to=[sender, evidence("to", "recruiter@example.com")],
            preferred=["cc@example.com"],
        )
        self.assertEqual(selected.to_email, "recruiter@example.com")

        failed = self.evaluate(
            to=[sender, evidence("to", "one@example.com"), evidence("to", "two@example.com")],
            preferred=["cc@example.com"],
        )
        self.assertIsNone(failed.to_email)
        self.assertTrue(failed.should_mark_failed)
        self.assertEqual(failed.recommended_skip_reason, "missing_to_or_cc")
        self.assertIn("manual selection", failed.reason)

    def test_candidate_and_preferred_cc_are_unioned_deduped_and_capped_in_priority_order(self) -> None:
        decision = self.evaluate(
            to=[evidence("to", "recruiter@example.com")],
            cc=[evidence("cc", "candidate-one@example.com"), evidence("cc", "candidate-two@example.com")],
            preferred=["candidate-one@example.com", "preferred-one@example.com", "preferred-two@example.com"],
            default=["default@example.com"],
        )

        self.assertEqual(
            decision.cc_email,
            "candidate-one@example.com, candidate-two@example.com, preferred-one@example.com",
        )
        self.assertNotIn("default@example.com", decision.cc_email or "")

    def test_default_only_fires_when_candidate_and_preferred_are_both_empty(self) -> None:
        defaulted = self.evaluate(
            to=[evidence("to", "recruiter@example.com")],
            default=["default-one@example.com", "default-two@example.com"],
        )
        self.assertEqual(defaulted.cc_email, "default-one@example.com, default-two@example.com")

        candidate = self.evaluate(
            to=[evidence("to", "recruiter@example.com")],
            cc=[evidence("cc", "candidate@example.com")],
            default=["default@example.com"],
        )
        self.assertEqual(candidate.cc_email, "candidate@example.com")

        preferred = self.evaluate(
            to=[evidence("to", "recruiter@example.com")],
            preferred=["preferred@example.com"],
            default=["default@example.com"],
        )
        self.assertEqual(preferred.cc_email, "preferred@example.com")

    def test_to_cc_collision_is_removed_without_blocking_when_another_cc_remains(self) -> None:
        decision = self.evaluate(
            to=[evidence("to", "hr@horizonsoftech.net", "sender_header")],
            cc=[
                evidence("cc", " HR@HorizonSoftech.net "),
                evidence("cc", "manager@horizonsoftech.net"),
            ],
        )

        self.assertEqual(decision.cc_email, "manager@horizonsoftech.net")
        self.assertEqual(decision.status, "safe")
        self.assertTrue(decision.is_sendable_candidate)
        self.assertFalse(decision.needs_manual_confirmation)

    def test_everything_empty_reports_missing_default_hint(self) -> None:
        decision = self.evaluate()
        self.assertTrue(decision.should_mark_failed)
        self.assertEqual(decision.recommended_state, "failed")
        self.assertEqual(decision.recommended_skip_reason, "missing_default_employer_cc")
        self.assertIn("Default CC is missing in Execution Control", decision.reason)

    def test_gmail_adapter_promotes_forwarded_employer_sender_to_cc(self) -> None:
        extracted = extract_recipient_routing_candidates(
            "Recruiter <recruiter@example.com>",
            "Role",
            "From: Employer Contact <manager@horizonsoftech.net>\nPlease reply.",
        )
        forwarded = [item for item in extracted.cc_candidates if item.source == "forwarded_from"]
        self.assertEqual([item.email for item in forwarded], ["manager@horizonsoftech.net"])

    def test_legacy_gmail_input_and_phase0_wrapper_use_the_shared_core(self) -> None:
        decision = RoutingPolicyService().evaluate(
            RoutingPolicyInput(
                sender="Prashanth Kinnera <kprashanth@horizonsoftech.net>",
                subject="Java Microservices RPA Developer",
                body=EMAIL_30_BODY,
            )
        )
        phase0 = analyze_recipient_routing(
            "Prashanth Kinnera <kprashanth@horizonsoftech.net>",
            "Java Microservices RPA Developer",
            EMAIL_30_BODY,
        )
        self.assertEqual((decision.to_email, decision.cc_email), (phase0.to_email, phase0.cc_email))
        self.assertEqual(decision.to_email, "sudarsan@cystemslogic.com")
        self.assertEqual(decision.cc_email, "kprashanth@horizonsoftech.net")

    def test_routing_decision_assignment_matches_routing_result_assignment(self) -> None:
        runtime = RoutingRuntimeService(
            RoutingRuntimeDeps(owner_id="default-owner", get_employer_domains=lambda db: [])
        )
        decision = self.evaluate(
            to=[evidence("to", "recruiter@example.com")],
            cc=[evidence("cc", "employer@example.com")],
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

        runtime.apply_routing_decision(from_decision, decision)
        runtime.apply_routing_result(from_result, decision.to_routing_result())

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
