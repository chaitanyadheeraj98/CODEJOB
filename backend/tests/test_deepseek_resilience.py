"""C5: survive DeepSeek's documented behaviour above the concurrency limit.

DeepSeek's limits are by *concurrency*, not tokens per minute, and they are per
account rather than per key. Above the limit it does not reject: it holds the
connection open - blank lines on a non-streaming call, `: keep-alive` comments
on a streaming one - and it answers 429 when it will not queue you.

So the two shapes that mean "busy, ask again" are a 429 and a response that
arrives with no content. Both are retried. Everything else is handed back
unchanged, because retrying a bad request spends the budget three times and
fails identically.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

import httpx
from openai import APIConnectionError, APITimeoutError, BadRequestError, RateLimitError

from app import tenancy
from app.ai import deepseek_client
from app.ai.deepseek_client import DeepSeekEmptyResponse
from app.config import settings


def _rate_limited() -> RateLimitError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(429, request=request)
    return RateLimitError("too many concurrent requests", response=response, body=None)


def _bad_request() -> BadRequestError:
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError("bad request", response=response, body=None)


class RetryTests(unittest.TestCase):
    def setUp(self):
        # No real sleeping: the backoff is asserted by attempt count, and a
        # test that waits for it would add seconds to every run.
        self._sleep = patch.object(deepseek_client, "_sleep_before_retry", lambda _a: None)
        self._sleep.start()

    def tearDown(self):
        self._sleep.stop()

    def test_a_429_is_retried_and_can_succeed(self):
        calls = []

        def send():
            calls.append(1)
            if len(calls) < 3:
                raise _rate_limited()
            return "answer"

        self.assertEqual(deepseek_client._call_with_retries(send, what="t"), "answer")
        self.assertEqual(len(calls), 3)

    def test_an_empty_keep_alive_response_is_retried(self):
        """Above the limit the body is whitespace holding the connection open.

        Parsing that would raise a JSON error and burn a ladder rung on a
        non-answer.
        """
        calls = []

        def send():
            calls.append(1)
            if len(calls) == 1:
                raise DeepSeekEmptyResponse("empty")
            return "answer"

        self.assertEqual(deepseek_client._call_with_retries(send, what="t"), "answer")
        self.assertEqual(len(calls), 2)

    def test_transport_failures_are_retried(self):
        request = httpx.Request("POST", "https://api.deepseek.com/")
        for error in (APITimeoutError(request=request), APIConnectionError(request=request)):
            calls = []

            def send(exc=error):
                calls.append(1)
                if len(calls) == 1:
                    raise exc
                return "answer"

            self.assertEqual(deepseek_client._call_with_retries(send, what="t"), "answer")

    def test_a_bad_request_is_not_retried(self):
        """Spending three attempts to fail identically helps nobody."""
        calls = []

        def send():
            calls.append(1)
            raise _bad_request()

        with self.assertRaises(BadRequestError):
            deepseek_client._call_with_retries(send, what="t")
        self.assertEqual(len(calls), 1)

    def test_the_attempt_budget_is_bounded_and_the_last_error_surfaces(self):
        calls = []

        def send():
            calls.append(1)
            raise _rate_limited()

        with patch.object(settings, "deepseek_max_attempts", 3):
            with self.assertRaises(RateLimitError):
                deepseek_client._call_with_retries(send, what="t")
        self.assertEqual(len(calls), 3)

    def test_one_attempt_means_no_retry(self):
        calls = []

        def send():
            calls.append(1)
            raise _rate_limited()

        with patch.object(settings, "deepseek_max_attempts", 1):
            with self.assertRaises(RateLimitError):
                deepseek_client._call_with_retries(send, what="t")
        self.assertEqual(len(calls), 1)

    def test_the_backoff_is_jittered(self):
        """Without jitter a crowd that hit the limit together retries together
        and recreates it - the same reasoning as C2's admission queue."""
        self._sleep.stop()
        try:
            delays = []
            with patch.object(deepseek_client.time, "sleep", delays.append), \
                    patch.object(settings, "deepseek_retry_base_seconds", 1.0):
                for _ in range(12):
                    deepseek_client._sleep_before_retry(0)
            self.assertGreater(len(set(delays)), 6, "the delay must actually vary")
            self.assertTrue(all(0.5 <= d <= 1.5 for d in delays), delays)
        finally:
            self._sleep.start()


class UserIdTests(unittest.TestCase):
    """DeepSeek segments rate limits by `user`, so one tenant's burst must not
    eat another's headroom once quota is expanded."""

    def test_the_owner_is_sent(self):
        reset = tenancy.set_owner_id("usr_c0dbbdd2f38e4f06b799b6658a533973")
        try:
            self.assertEqual(
                deepseek_client._request_user_id(), "usr_c0dbbdd2f38e4f06b799b6658a533973"
            )
        finally:
            tenancy.reset_owner_id(reset)

    def test_it_matches_the_format_the_api_requires(self):
        import re

        reset = tenancy.set_owner_id("usr_abc123")
        try:
            value = deepseek_client._request_user_id()
        finally:
            tenancy.reset_owner_id(reset)
        self.assertRegex(value, r"^[a-zA-Z0-9_-]+$")
        self.assertLessEqual(len(value), 512)

    def test_anything_unexpected_is_sanitised_rather_than_forwarded(self):
        """The owner id is opaque by construction, so this should never fire.

        It exists because the cost of being wrong is leaking an identifier to a
        third party, and the cost of the check is nothing.
        """
        reset = tenancy.set_owner_id("someone@example.com")
        try:
            self.assertEqual(deepseek_client._request_user_id(), "someoneexamplecom")
        finally:
            tenancy.reset_owner_id(reset)

    def test_it_is_truncated_to_the_documented_maximum(self):
        reset = tenancy.set_owner_id("u" * 900)
        try:
            self.assertEqual(len(deepseek_client._request_user_id()), 512)
        finally:
            tenancy.reset_owner_id(reset)

    def test_it_can_be_switched_off(self):
        with patch.object(settings, "deepseek_send_user_id", False):
            self.assertIsNone(deepseek_client._request_user_id())


class RequestShapeTests(unittest.TestCase):
    """The retry and the user id have to reach the actual request."""

    def _fake_client(self, content: str, seen: list):
        def create(**kwargs):
            seen.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
                usage=None,
                model="deepseek-v4-flash",
            )

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def test_chat_completion_sends_the_user_and_retries_empties(self):
        seen: list = []
        attempts = {"n": 0}

        def create(**kwargs):
            seen.append(kwargs)
            attempts["n"] += 1
            body = "   " if attempts["n"] == 1 else "an answer"
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=body), finish_reason="stop")],
                usage=None,
                model="deepseek-v4-flash",
            )

        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        reset = tenancy.set_owner_id("usr_probe")
        try:
            with patch.object(deepseek_client, "_build_client", lambda **_k: client), \
                    patch.object(deepseek_client, "_sleep_before_retry", lambda _a: None), \
                    patch.object(settings, "deepseek_api_key", "k"):
                result = deepseek_client.deepseek_chat_completion("sys", "user")
        finally:
            tenancy.reset_owner_id(reset)

        self.assertEqual(result, "an answer")
        self.assertEqual(len(seen), 2, "the whitespace body must have been retried")
        self.assertEqual(seen[0]["user"], "usr_probe")

    def test_json_completion_sends_the_user(self):
        seen: list = []
        client = self._fake_client('{"ok": true}', seen)
        reset = tenancy.set_owner_id("usr_probe")
        try:
            with patch.object(deepseek_client, "_build_client", lambda **_k: client), \
                    patch.object(settings, "deepseek_api_key", "k"):
                payload = deepseek_client.deepseek_json_completion("sys", "user")
        finally:
            tenancy.reset_owner_id(reset)
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(seen[0]["user"], "usr_probe")

    def test_no_user_is_sent_when_the_switch_is_off(self):
        seen: list = []
        client = self._fake_client('{"ok": true}', seen)
        with patch.object(deepseek_client, "_build_client", lambda **_k: client), \
                patch.object(settings, "deepseek_api_key", "k"), \
                patch.object(settings, "deepseek_send_user_id", False):
            deepseek_client.deepseek_json_completion("sys", "user")
        self.assertNotIn("user", seen[0])


if __name__ == "__main__":
    unittest.main()
