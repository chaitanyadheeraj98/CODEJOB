"""The provider seam.

These tests exist because the seam absorbs three things that used to be someone
else's problem and now silently become nobody's if they regress:

* DeepSeek has no `json_schema` mode. The only thing standing between a
  well-formed-but-wrong-shape response and a bad gate verdict is client-side
  validation, so an invalid shape must be an *error code*, never a crash and never
  an accepted payload.
* The old path's worst case was timeout x retries. The budget here covers the whole
  ladder, so a second rung must not buy a second worst case.
* The ladder ships inert. One rung, no escalation, until a measurement earns more.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.intent_provider import effort_ladder, intent_chat_json
from app.config import settings

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent_type": {"type": "string", "enum": ["a", "b"]},
        "confidence": {"type": "number"},
    },
    "required": ["intent_type", "confidence"],
    "additionalProperties": False,
}

VALID = {"intent_type": "a", "confidence": 0.9}


def _result(payload: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        payload=payload,
        model="deepseek-v4-flash",
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=20,
        duration_ms=10,
        response_hash="hash",
        repair_attempted=False,
        prompt_cache_hit_tokens=64,
        prompt_cache_miss_tokens=36,
    )


class IntentProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        for name, value in (
            ("intent_gate_provider", "deepseek"),
            ("intent_gate_effort_ladder", "disabled"),
            ("intent_gate_escalate_on_disagreement", False),
            ("intent_gate_timeout_seconds", 12.0),
            ("deepseek_api_key", "test-key"),
        ):
            previous = getattr(settings, name)
            setattr(settings, name, value)
            self.addCleanup(setattr, settings, name, previous)

    def _call(self, **kwargs):
        return intent_chat_json(
            system_prompt="SYSTEM",
            user_prompt="USER",
            schema=SCHEMA,
            **kwargs,
        )

    def test_intent_provider_dispatches_on_setting(self) -> None:
        settings.intent_gate_provider = "groq"
        with patch("app.ai.intent_provider.groq_chat_json", return_value=(VALID, None)) as groq:
            payload, error, usage = self._call()
        self.assertEqual(payload, VALID)
        self.assertIsNone(error)
        self.assertEqual(usage.provider, "groq")
        groq.assert_called_once()

        settings.intent_gate_provider = "taxonomy"
        with patch("app.ai.intent_provider.groq_chat_json") as groq, patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics"
        ) as deepseek:
            payload, error, usage = self._call()
        self.assertIsNone(payload)
        self.assertEqual(error, "provider_disabled")
        # "taxonomy" must cost nothing: no network to either vendor.
        groq.assert_not_called()
        deepseek.assert_not_called()

        settings.intent_gate_provider = "deepseek"
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ) as deepseek:
            payload, error, usage = self._call()
        self.assertEqual(payload, VALID)
        self.assertEqual(usage.provider, "deepseek")
        deepseek.assert_called_once()

    def test_intent_provider_validates_schema(self) -> None:
        """Well-formed JSON, wrong shape. Groq's server enforced this; nothing does now."""
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result({"intent_type": "not_in_enum", "confidence": "high"}),
        ):
            payload, error, _usage = self._call()
        self.assertIsNone(payload)
        self.assertEqual(error, "deepseek_invalid_shape")

    def test_intent_provider_handles_empty_content(self) -> None:
        """A documented DeepSeek failure mode - an error code, not an exception."""
        from app.ai.deepseek_client import DeepSeekJSONError

        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            side_effect=DeepSeekJSONError("DeepSeek returned empty content", raw_content=""),
        ):
            payload, error, _usage = self._call()
        self.assertIsNone(payload)
        self.assertEqual(error, "deepseek_empty_content")

    def test_intent_provider_timeout_is_bounded(self) -> None:
        """One budget per email, not per rung.

        A two-rung ladder that passed the full timeout to each rung would double the
        worst case - exactly the bug the single budget exists to prevent.
        """
        settings.intent_gate_effort_ladder = "disabled,high"
        settings.intent_gate_timeout_seconds = 4.0
        timeouts: list[float] = []

        def record(*_args, **kwargs):
            timeouts.append(kwargs["timeout_seconds"])
            raise TimeoutError("boom")

        with patch("app.ai.intent_provider.deepseek_json_completion_with_diagnostics", side_effect=record):
            self._call()

        self.assertEqual(len(timeouts), 2)
        self.assertLessEqual(sum(timeouts), 8.0)
        self.assertLessEqual(timeouts[0], 4.0)
        # The second rung only gets what the first left behind.
        self.assertLessEqual(timeouts[1], timeouts[0])

    def test_effort_ladder_ships_inert(self) -> None:
        self.assertEqual(effort_ladder(), ("disabled",))
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ) as deepseek:
            _payload, _error, usage = self._call()
        deepseek.assert_called_once()
        self.assertEqual(deepseek.call_args.kwargs["thinking"], "disabled")
        self.assertEqual(usage.attempts, 1)
        self.assertFalse(usage.escalated)

    def test_effort_ladder_escalates_on_hard_failure(self) -> None:
        settings.intent_gate_effort_ladder = "disabled,high"
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            side_effect=[_result({"wrong": "shape"}), _result(VALID)],
        ) as deepseek:
            payload, error, usage = self._call()
        self.assertEqual(payload, VALID)
        self.assertIsNone(error)
        self.assertEqual(deepseek.call_count, 2)
        self.assertEqual(deepseek.call_args_list[0].kwargs["thinking"], "disabled")
        self.assertEqual(deepseek.call_args_list[1].kwargs["thinking"], "enabled")
        self.assertTrue(usage.escalated)
        self.assertEqual(usage.rung, "high")

    def test_effort_ladder_escalates_on_disagreement_only_when_enabled(self) -> None:
        settings.intent_gate_effort_ladder = "disabled,high"
        disagrees = lambda _payload: False  # noqa: E731 - a one-expression stub

        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            side_effect=[_result(VALID), _result(VALID)],
        ) as deepseek:
            payload, _error, _usage = self._call(agrees_with_taxonomy=disagrees)
        self.assertEqual(payload, VALID)
        self.assertEqual(deepseek.call_count, 1, "escalation must stay off until opted into")

        settings.intent_gate_escalate_on_disagreement = True
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            side_effect=[_result(VALID), _result(VALID)],
        ) as deepseek:
            payload, _error, usage = self._call(agrees_with_taxonomy=disagrees)
        self.assertEqual(payload, VALID)
        self.assertEqual(deepseek.call_count, 2)
        self.assertTrue(usage.escalated)

    def test_agreement_never_escalates_past_the_last_rung(self) -> None:
        """A one-rung ladder cannot escalate, whatever the flag says."""
        settings.intent_gate_escalate_on_disagreement = True
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ) as deepseek:
            payload, error, _usage = self._call(agrees_with_taxonomy=lambda _payload: False)
        self.assertEqual(payload, VALID)
        self.assertIsNone(error)
        self.assertEqual(deepseek.call_count, 1)

    def test_ladder_exhaustion_returns_the_last_error(self) -> None:
        settings.intent_gate_effort_ladder = "disabled,high"
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            side_effect=[_result({"wrong": "shape"}), _result({"wrong": "shape"})],
        ):
            payload, error, usage = self._call()
        self.assertIsNone(payload)
        self.assertEqual(error, "deepseek_invalid_shape")
        self.assertEqual(usage.attempts, 2)

    def test_missing_key_is_an_error_code_not_a_crash(self) -> None:
        settings.deepseek_api_key = ""
        payload, error, usage = self._call()
        self.assertIsNone(payload)
        self.assertEqual(error, "missing_deepseek_api_key")
        self.assertIsNone(usage)

    def test_prompt_carries_the_json_word_and_an_example_shape(self) -> None:
        """A documented json_object requirement, not a style preference."""
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ) as deepseek:
            self._call()
        system_prompt = deepseek.call_args.args[0]
        self.assertIn("json", system_prompt.lower())
        self.assertIn('"intent_type"', system_prompt)
        self.assertTrue(system_prompt.startswith("SYSTEM"), "the caller's prefix must stay first")

    def test_appended_json_instruction_is_identical_across_calls(self) -> None:
        """The cache prefix is only worth anything if it is byte-identical."""
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ) as deepseek:
            self._call()
            intent_chat_json(system_prompt="SYSTEM", user_prompt="DIFFERENT", schema=SCHEMA)
        first = deepseek.call_args_list[0].args[0]
        second = deepseek.call_args_list[1].args[0]
        self.assertEqual(first, second)

    def test_usage_reports_the_prompt_cache_split(self) -> None:
        with patch(
            "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
            return_value=_result(VALID),
        ):
            _payload, _error, usage = self._call()
        self.assertEqual(usage.prompt_cache_hit_tokens, 64)
        self.assertEqual(usage.prompt_cache_miss_tokens, 36)


if __name__ == "__main__":
    unittest.main()
