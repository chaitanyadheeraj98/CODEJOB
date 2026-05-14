import unittest

from app.ai.draft_quality import assess_draft_quality


class DraftQualityTests(unittest.TestCase):
    def test_scores_strong_draft_with_complete_signals(self) -> None:
        quality = assess_draft_quality(
            draft_text="Hi Alex,\n\nThanks for sharing this role.",
            ai_score=0.9,
            routing_confidence=0.9,
            resume_context_status="injected",
            recipient_email="to@example.com",
            cc_email="cc@example.com",
            draft_ai_error=None,
        )
        self.assertTrue(quality.content_valid)
        self.assertEqual(quality.greeting_compliance, "compliant")
        self.assertIn(quality.label, {"Strong", "Excellent"})

    def test_flags_empty_draft_and_missing_greeting(self) -> None:
        quality = assess_draft_quality(
            draft_text="",
            ai_score=0.2,
            routing_confidence=0.2,
            resume_context_status="extract_failed",
            recipient_email=None,
            cc_email=None,
            draft_ai_error="fallback used",
        )
        self.assertFalse(quality.content_valid)
        self.assertEqual(quality.greeting_compliance, "missing")
        self.assertIn("draft_empty", quality.issues)
        self.assertEqual(quality.label, "Risky")


if __name__ == "__main__":
    unittest.main()
