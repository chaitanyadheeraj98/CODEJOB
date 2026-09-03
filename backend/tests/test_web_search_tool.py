import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.config import settings
from app.mcp_server.tools.web_search import MAX_SNIPPET_CHARS, MAX_TITLE_CHARS, search_web


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def fake_results(count: int) -> dict:
    return {
        "results": [
            {
                "url": f"https://example.com/{index}",
                "title": f"Result {index}",
                "content": f"Snippet {index}",
                "score": 1.0 - index / 100,
            }
            for index in range(count)
        ]
    }


class SearchWebPayloadTests(unittest.TestCase):
    def search(self, payload: dict, **kwargs) -> dict:
        with patch("app.mcp_server.tools.web_search.httpx.get", return_value=FakeResponse(payload)):
            with patch.object(settings, "searxng_url", "http://searxng.local"):
                return search_web("h1b transfer", **kwargs)

    def test_payload_carries_the_render_discriminator(self) -> None:
        # Without an action the message is dropped from the thread entirely:
        # visibleMessages keeps only what a registry claims.
        result = self.search(fake_results(2))

        self.assertEqual(result["action"], "search_web")
        self.assertEqual(result["query"], "h1b transfer")

    def test_each_result_carries_title_snippet_and_the_delimited_text(self) -> None:
        row = self.search(fake_results(1))["results"][0]

        self.assertEqual(row["title"], "Result 0")
        self.assertEqual(row["snippet"], "Snippet 0")
        self.assertEqual(row["url"], "https://example.com/0")
        # The delimiters stay: splitting the fields out is for the component,
        # and must not change what the model reads.
        self.assertIn("<untrusted_web_data>", row["untrusted_web_data"])
        self.assertIn("</untrusted_web_data>", row["untrusted_web_data"])
        self.assertIn("Result 0", row["untrusted_web_data"])

    def test_results_are_capped_by_the_configured_maximum(self) -> None:
        with patch.object(settings, "chat_web_search_max_results", 3):
            self.assertEqual(len(self.search(fake_results(10), max_results=99)["results"]), 3)

    def test_title_and_snippet_are_clipped(self) -> None:
        payload = {"results": [{"url": "https://example.com", "title": "T" * 900, "content": "S" * 900}]}
        row = self.search(payload)["results"][0]

        self.assertEqual(len(row["title"]), MAX_TITLE_CHARS)
        self.assertEqual(len(row["snippet"]), MAX_SNIPPET_CHARS)

    def test_missing_title_and_content_become_empty_strings(self) -> None:
        row = self.search({"results": [{"url": "https://example.com"}]})["results"][0]

        self.assertEqual(row["title"], "")
        self.assertEqual(row["snippet"], "")

    def test_an_empty_query_is_refused(self) -> None:
        self.assertIn("error", search_web("   "))


if __name__ == "__main__":
    unittest.main()
