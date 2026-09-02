"""DeepSeek's failure statuses must stay told apart, and terminal ones must stop.

`_deepseek_error_code` mapped four of DeepSeek's seven documented statuses onto a
single `deepseek_unavailable`, and the ladder retried all seven identically. The
case that bites is 402: when the account balance runs out, every email burned every
rung, exhausted the timeout budget, fell back to the rules taxonomy, and persisted a
`gate_error` indistinguishable from a transient 503 - so the recorded evidence
pointed at a DeepSeek outage rather than at a billing page.

That is the failure `gate_error` exists to prevent, which is why this is asserted
per status rather than left to a single happy-path test. The table below is the
contract: status in, code out, and whether the ladder is allowed to try again.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

import httpx
from openai import (
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
    UnprocessableEntityError,
)

from app.ai.intent_provider import (
    _TERMINAL_ERRORS,
    _deepseek_error_code,
    _deepseek_ladder,
)

_REQUEST = httpx.Request("POST", "https://api.deepseek.com/chat/completions")


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=_REQUEST)


# status, SDK exception class, expected code, may the ladder retry it
CASES = (
    (400, BadRequestError, "deepseek_bad_request", False),
    (401, AuthenticationError, "deepseek_auth_failed", False),
    (402, APIStatusError, "deepseek_insufficient_balance", False),
    (422, UnprocessableEntityError, "deepseek_invalid_parameters", False),
    (429, RateLimitError, "deepseek_rate_limited", True),
    (500, InternalServerError, "deepseek_unavailable", True),
    (503, InternalServerError, "deepseek_unavailable", True),
)

SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {"intent_type": {"type": "string"}},
    "required": ["intent_type"],
}


class ErrorCodeMappingTests(unittest.TestCase):
    def test_each_status_maps_to_its_own_code(self) -> None:
        for status, exception_class, expected, _ in CASES:
            with self.subTest(status=status):
                exc = exception_class("boom", response=_response(status), body=None)
                self.assertEqual(_deepseek_error_code(exc), expected)

    def test_the_four_human_or_hopeless_statuses_are_terminal(self) -> None:
        for status, _, code, retryable in CASES:
            with self.subTest(status=status):
                self.assertEqual(code not in _TERMINAL_ERRORS, retryable)

    def test_a_timeout_is_still_a_timeout(self) -> None:
        """Guards the pre-existing codes this change must not disturb."""
        self.assertEqual(
            _deepseek_error_code(APITimeoutError(request=_REQUEST)), "deepseek_timeout"
        )

    def test_an_unrecognised_exception_keeps_its_class_name(self) -> None:
        self.assertEqual(
            _deepseek_error_code(ValueError("odd")), "deepseek_error:ValueError"
        )


class LadderStopsOnTerminalErrorTests(unittest.TestCase):
    """The mapping is only half of it - the ladder has to act on the distinction."""

    def _run_ladder(self, exc: Exception, ladder: str = "disabled,high,deepseek_pro"):
        calls: list[str] = []

        def _raise(*_args: object, **kwargs: object) -> object:
            calls.append(str(kwargs.get("thinking")))
            raise exc

        with (
            patch("app.ai.intent_provider.settings") as fake_settings,
            patch(
                "app.ai.intent_provider.deepseek_json_completion_with_diagnostics",
                side_effect=_raise,
            ),
            patch("app.ai.intent_provider.effort_ladder", return_value=tuple(ladder.split(","))),
        ):
            fake_settings.deepseek_api_key = "key"
            fake_settings.intent_gate_model = "deepseek-v4-flash"
            fake_settings.deepseek_model_fast = "deepseek-v4-flash"
            fake_settings.intent_gate_timeout_seconds = 12.0
            fake_settings.intent_gate_escalate_on_disagreement = False
            payload, error, usage = _deepseek_ladder(
                system_prompt="classify",
                user_prompt="body",
                schema=SCHEMA,
                model=None,
                max_tokens=100,
                agrees_with_taxonomy=None,
            )
        return payload, error, usage, calls

    def test_a_spent_balance_stops_after_one_attempt(self) -> None:
        exc = APIStatusError("insufficient balance", response=_response(402), body=None)
        payload, error, usage, calls = self._run_ladder(exc)

        self.assertIsNone(payload)
        self.assertEqual(error, "deepseek_insufficient_balance")
        self.assertEqual(len(calls), 1, "the remaining rungs must not be attempted")
        self.assertIsNotNone(usage)
        assert usage is not None
        self.assertEqual(usage.attempts, 1)

    def test_a_dead_key_stops_after_one_attempt(self) -> None:
        exc = AuthenticationError("wrong api key", response=_response(401), body=None)
        _, error, _, calls = self._run_ladder(exc)

        self.assertEqual(error, "deepseek_auth_failed")
        self.assertEqual(len(calls), 1)

    def test_a_server_error_still_walks_the_whole_ladder(self) -> None:
        """The retryable path must be untouched - this is what the ladder is for."""
        exc = InternalServerError("overloaded", response=_response(503), body=None)
        _, error, _, calls = self._run_ladder(exc)

        self.assertEqual(error, "deepseek_unavailable")
        self.assertEqual(len(calls), 3, "all three rungs should have been tried")

    def test_a_rate_limit_retries_and_waits_first(self) -> None:
        exc = RateLimitError("slow down", response=_response(429), body=None)
        slept: list[float] = []
        with patch("app.ai.intent_provider.time.sleep", side_effect=slept.append):
            _, error, _, calls = self._run_ladder(exc)

        self.assertEqual(error, "deepseek_rate_limited")
        self.assertEqual(len(calls), 3)
        self.assertTrue(slept, "a 429 must back off before the next rung")
        self.assertTrue(all(delay > 0 for delay in slept))
        self.assertLess(
            sum(slept), 12.0, "backoff must stay inside the per-email budget"
        )


if __name__ == "__main__":
    unittest.main()
