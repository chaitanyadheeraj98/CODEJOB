import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.gates.job_description_gate import classify_email_intent
from app.job_intent_learning import JobIntentLearningSignal
from app.services.gmail_group_source_service import TrustedGroupContext
from app.taxonomy.job_description_taxonomy import classify_job_description_taxonomy


class JobDescriptionGateTests(unittest.TestCase):
    def test_taxonomy_passes_real_job_with_unsubscribe_footer(self) -> None:
        decision = classify_job_description_taxonomy(
            sender="jobs@googlegroups.com",
            subject="Java Developer - Texas",
            body=(
                "Position: Java Developer\n"
                "Job Description: Build Spring Boot services.\n"
                "Required Skills: Java, Spring Boot, Kafka.\n"
                "Location: Texas\n"
                "Duration: 12 months\n"
                "Rate: $60/hr C2C\n"
                "Share resume and visa status.\n"
                "To unsubscribe from this group, send email to stop@example.com"
            ),
        )

        self.assertEqual(decision.intent_type, "recruiter_job_requirement")
        self.assertEqual(decision.action, "process_for_queue")
        self.assertGreaterEqual(decision.confidence, 0.70)
        self.assertIn("weak_footer:unsubscribe", decision.negative_evidence)

    def test_taxonomy_skips_candidate_marketing_not_newsletter(self) -> None:
        decision = classify_job_description_taxonomy(
            sender="benchsales@example.com",
            subject="Lead Data Scientist",
            body=(
                "Please find attached resume.\n"
                "My candidate visa is GC.\n"
                "Please share relevant requirements.\n"
            ),
        )

        self.assertEqual(decision.intent_type, "candidate_marketing_or_hotlist")
        self.assertEqual(decision.action, "skip")

    def test_taxonomy_uses_approved_learning_signals_only_when_approved(self) -> None:
        without_signal = classify_job_description_taxonomy(
            sender="jobs@example.com",
            subject="Data Engineer | Project Falcon",
            body="TalentX brief attached for review.",
        )
        with_signal = classify_job_description_taxonomy(
            sender="jobs@example.com",
            subject="Data Engineer | Project Falcon",
            body="TalentX brief attached for review.",
            approved_learning_signals=[
                JobIntentLearningSignal(
                    phrase="talentx brief",
                    polarity="positive_recruiter_jd",
                    confidence=0.88,
                )
            ],
        )

        self.assertEqual(without_signal.intent_type, "unknown")
        self.assertEqual(with_signal.intent_type, "recruiter_job_requirement")
        self.assertEqual(with_signal.action, "process_for_queue")

    def test_taxonomy_trusted_group_keeps_ambiguous_message_in_review(self) -> None:
        decision = classify_job_description_taxonomy(
            sender="poster@example.com",
            subject="[C2C-Corp2Corp-Jobs] FYI",
            body="Please review when you have time.",
            trusted_group_context=TrustedGroupContext(
                matched=True,
                group_id=1,
                group_name="C2C Corp2Corp Jobs",
                group_email="c2c-corp2corp-jobs@googlegroups.com",
                match_method="subject_prefix",
                confidence=0.8,
                trusted=True,
            ),
        )

        self.assertEqual(decision.intent_type, "unknown")
        self.assertEqual(decision.action, "needs_review")

    @patch(
        "app.gates.job_description_gate.classify_job_description_taxonomy",
        return_value=SimpleNamespace(
            intent_type="general_newsletter",
            action="skip",
            confidence=0.82,
            reason="Fallback taxonomy would skip this as newsletter.",
            evidence=[],
            negative_evidence=["weak_footer:unsubscribe"],
        ),
    )
    @patch(
        "app.gates.job_description_gate.groq_chat_json",
        return_value=(
            {
                "intent_type": "recruiter_job_requirement",
                "action": "process_for_queue",
                "confidence": 0.94,
                "reason": "Body is a recruiter-sent JD despite the Google Groups footer.",
                "evidence": ["job description", "rate", "share resume"],
                "negative_evidence": ["weak_footer:unsubscribe"],
                "learning_signals": [
                    {"phrase": "share resume", "polarity": "positive_recruiter_jd", "confidence": 0.84}
                ],
            },
            None,
        ),
    )
    def test_groq_enabled_overrides_taxonomy_skip(self, _mock_groq, _mock_taxonomy) -> None:
        decision = classify_email_intent(
            sender="jobs@googlegroups.com",
            subject="Frontend Developer",
            body="Job Description: React role. Share resume. Rate: $70/hr.",
            groq_enabled=True,
        )

        self.assertEqual(decision.provider, "groq")
        self.assertEqual(decision.intent_type, "recruiter_job_requirement")
        self.assertEqual(decision.action, "process_for_queue")
        self.assertEqual(decision.learned_signals[0].phrase, "share resume")

    @patch(
        "app.gates.job_description_gate.classify_job_description_taxonomy",
        return_value=SimpleNamespace(
            intent_type="unknown",
            action="needs_review",
            confidence=0.62,
            reason="Uncertain taxonomy result.",
            evidence=["role_keyword:frontend"],
            negative_evidence=[],
        ),
    )
    @patch("app.gates.job_description_gate.groq_chat_json", return_value=(None, "groq_timeout"))
    def test_gate_falls_back_when_groq_fails(self, _mock_groq, _mock_taxonomy) -> None:
        decision = classify_email_intent(
            sender="jobs@googlegroups.com",
            subject="Frontend Developer",
            body=(
                "Position: Frontend Developer\n"
                "Required Skills: React, Node.js\n"
                "Location: NY\n"
                "Rate: $70/hr\n"
            ),
            groq_enabled=True,
        )

        self.assertEqual(decision.provider, "groq_fallback_taxonomy")
        self.assertEqual(decision.intent_type, "unknown")
        self.assertEqual(decision.action, "needs_review")
        self.assertEqual(decision.error, "groq_timeout")


if __name__ == "__main__":
    unittest.main()
