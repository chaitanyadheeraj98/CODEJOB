import unittest
from unittest.mock import patch

from app.services.telegram_format import chunk, escape, format_answer
from app.telegram_bot import TelegramAPIError, TelegramTransport


class TelegramFormatTests(unittest.TestCase):
    def test_escape_preserves_untrusted_subject_as_text(self) -> None:
        self.assertEqual(escape("<script> &"), "&lt;script&gt; &amp;")

    def test_long_answer_is_chunked_without_truncation(self) -> None:
        text = ("a" * 2998 + "\n\n") * 3
        parts = chunk(text)
        self.assertEqual("".join(parts), text)
        self.assertEqual([len(part) for part in parts], [3000, 3000, 3000])
        self.assertTrue(all(len(part) <= 4096 for part in parts))

    def test_pre_block_is_reopened_when_it_exceeds_the_limit(self) -> None:
        parts = chunk("<pre>" + "x" * 5000 + "</pre>")
        self.assertTrue(all(part.startswith("<pre>") and part.endswith("</pre>") for part in parts))
        self.assertTrue(all(len(part) <= 4096 for part in parts))

    def test_assistant_markdown_becomes_safe_telegram_html(self) -> None:
        self.assertEqual(
            format_answer("**Role** <Lead> & `Python`\n```x < y```"),
            "<b>Role</b> &lt;Lead&gt; &amp; <code>Python</code>\n<pre>x &lt; y</pre>",
        )


class TelegramTransportTests(unittest.TestCase):
    def test_parse_error_falls_back_to_plain_text(self) -> None:
        transport = TelegramTransport("token")
        calls: list[dict] = []

        def post(_method: str, payload: dict) -> dict:
            calls.append(payload)
            if len(calls) == 1:
                raise TelegramAPIError(400, "Bad Request: can't parse entities")
            return {"ok": True, "result": {"message_id": 4}}

        transport._post_json = post  # type: ignore[method-assign]
        message_id = transport.send_message(1, "<b>Subject:</b> &lt;script&gt; &amp;")

        self.assertEqual(message_id, 4)
        self.assertEqual(calls[0]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", calls[1])
        self.assertEqual(calls[1]["text"], "Subject: <script> &")

    def test_429_sleeps_and_retries_once(self) -> None:
        transport = TelegramTransport("token")
        calls = 0

        def request_json(_method: str, _payload: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TelegramAPIError(429, "Too Many Requests", 3)
            return {"ok": True}

        transport._request_json = request_json  # type: ignore[method-assign]
        with patch("app.telegram_bot.time.sleep") as sleep:
            transport._post_json("sendMessage", {})

        self.assertEqual(calls, 2)
        sleep.assert_called_once_with(3)


if __name__ == "__main__":
    unittest.main()
