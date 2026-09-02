"""Gate behaviour across `deepseek` / `groq` / `taxonomy`, and the two inert levers.

The gate decides what enters the queue at all, so the properties worth pinning are
not "does DeepSeek work" but "does the gate still return a decision when it does
not", and "do the new settings really ship off".
"""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.config import settings
from app.gates.job_description_gate import (
    GATE_SYSTEM_PROMPT,
    classify_email_intent,
    llm_decided,
)
from app.runtime_state import runtime_state

TAXONOMY = SimpleNamespace(
    intent_type="unknown",
    action="needs_review",
    confidence=0.62,
    reason="Uncertain taxonomy result.",
    evidence=["role_keyword:frontend"],
    negative_evidence=[],
)

MODEL_PAYLOAD = {
    "intent_type": "recruiter_job_requirement",
    "action": "process_for_queue",
    "confidence": 0.94,
    "reason": "Body is a recruiter-sent JD.",
    "evidence": ["job description", "rate"],
    "negative_evidence": [],
    "learning_signals": [{"phrase": "share resume", "polarity": "positive_recruiter_jd", "confidence": 0.8}],
}

BODY = "Position: Frontend Developer\nRequired Skills: React\nRate: $70/hr\n"


def _taxonomy(**overrides) -> SimpleNamespace:
    values = dict(TAXONOMY.__dict__)
    values.update(overrides)
    return SimpleNamespace(**values)


class IntentGateProviderSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (
            ("intent_gate_provider", "deepseek"),
            ("intent_gate_min_taxonomy_confidence", 0.0),
            ("intent_gate_escalate_on_disagreement", False),
            ("intent_gate_effort_ladder", "disabled"),
        ):
            previous = getattr(settings, name)
            setattr(settings, name, value)
            self.addCleanup(setattr, settings, name, previous)
        runtime_state.intent_gate_last_error = None
        runtime_state.intent_gate_last_success_at = None

    def _classify(self, **kwargs):
        return classify_email_intent(
            sender="jobs@googlegroups.com",
            subject="Frontend Developer",
            body=BODY,
            groq_enabled=True,
            **kwargs,
        )

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_deepseek_verdict_is_labelled_deepseek(self, _taxonomy_mock) -> None:
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ):
            decision = self._classify()
        self.assertEqual(decision.provider, "deepseek")
        self.assertEqual(decision.intent_type, "recruiter_job_requirement")
        self.assertTrue(llm_decided(decision.provider))

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_gate_falls_back_to_taxonomy_on_provider_error(self, _taxonomy_mock) -> None:
        """The run must complete. A dead provider degrades the answer, never the run."""
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(None, "deepseek_timeout", None),
        ):
            decision = self._classify()
        self.assertEqual(decision.provider, "deepseek_fallback_taxonomy")
        self.assertEqual(decision.intent_type, "unknown")
        self.assertEqual(decision.action, "needs_review")
        self.assertEqual(decision.error, "deepseek_timeout")
        self.assertFalse(llm_decided(decision.provider))

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_provider_taxonomy_is_not_labelled_a_failure(self, _taxonomy_mock) -> None:
        settings.intent_gate_provider = "taxonomy"
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(None, "provider_disabled", None),
        ):
            decision = self._classify()
        self.assertEqual(decision.provider, "taxonomy")
        self.assertEqual(decision.error, "provider_disabled")

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_groq_verdict_keeps_its_historical_label(self, _taxonomy_mock) -> None:
        """Rolling back must not rewrite the vocabulary already in the database."""
        settings.intent_gate_provider = "groq"
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ):
            decision = self._classify()
        self.assertEqual(decision.provider, "groq")

        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(None, "groq_timeout", None),
        ):
            decision = self._classify()
        self.assertEqual(decision.provider, "groq_fallback_taxonomy")

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy")
    def test_gate_short_circuits_only_above_threshold(self, taxonomy_mock) -> None:
        taxonomy_mock.return_value = _taxonomy(confidence=1.0)

        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ) as seam:
            decision = self._classify()
        self.assertEqual(seam.call_count, 1, "the shipped default (0.0) must never skip the model")
        self.assertNotEqual(decision.provider, "taxonomy_confident")

        settings.intent_gate_min_taxonomy_confidence = 1.0
        with patch("app.gates.job_description_gate.intent_chat_json") as seam:
            decision = self._classify()
        seam.assert_not_called()
        self.assertEqual(decision.provider, "taxonomy_confident")
        self.assertEqual(decision.intent_type, "unknown")

        # Just below the threshold still pays for the model.
        taxonomy_mock.return_value = _taxonomy(confidence=0.9)
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ) as seam:
            decision = self._classify()
        seam.assert_called_once()
        self.assertEqual(decision.provider, "deepseek")

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_system_prompt_is_invariant_across_emails(self, _taxonomy_mock) -> None:
        """The cache-prefix guarantee.

        DeepSeek's prompt cache keys on the literal prefix. If anything per-email
        ever leaks into the system prompt, every email pays full price on that
        segment and nothing in the logs says so.
        """
        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ) as seam:
            classify_email_intent(
                sender="one@example.com", subject="Java Developer", body="Rate: $60/hr", groq_enabled=True
            )
            classify_email_intent(
                sender="two@example.com", subject="Python Developer", body="Rate: $90/hr", groq_enabled=True
            )
        first = seam.call_args_list[0].kwargs["system_prompt"]
        second = seam.call_args_list[1].kwargs["system_prompt"]
        self.assertEqual(first, second)
        self.assertEqual(first, GATE_SYSTEM_PROMPT)
        for leaked in ("one@example.com", "Java Developer", "$60/hr"):
            self.assertNotIn(leaked, first)

    @patch("app.gates.job_description_gate.classify_job_description_taxonomy", return_value=TAXONOMY)
    def test_provider_never_sees_raw_contact_details(self, _taxonomy_mock) -> None:
        """Redaction lives above the seam, so every provider inherits it.

        This is the test that fails if someone ever moves a provider call above
        `prepare_job_intent_model_text` - at which point unredacted bodies start
        leaving the building.
        """
        previous = settings.groq_gate_redact_contact_info
        settings.groq_gate_redact_contact_info = True
        self.addCleanup(setattr, settings, "groq_gate_redact_contact_info", previous)

        with patch(
            "app.gates.job_description_gate.intent_chat_json",
            return_value=(MODEL_PAYLOAD, None, None),
        ) as seam:
            classify_email_intent(
                sender="recruiter@example.com",
                subject="Frontend Developer",
                body="Contact me at jane.doe@acme-staffing.com or 415-555-0142 about this role.",
                groq_enabled=True,
            )
        user_prompt = seam.call_args.kwargs["user_prompt"]
        body_section = user_prompt.split("Body:\n", 1)[1]
        self.assertNotIn("jane.doe@acme-staffing.com", body_section)
        self.assertNotIn("415-555-0142", body_section)
        self.assertNotRegex(body_section, r"[\w.+-]+@[\w-]+\.[\w.]+")
        self.assertIsNone(re.search(r"\d{3}[-.\s]\d{3}[-.\s]\d{4}", body_section))


if __name__ == "__main__":
    unittest.main()
