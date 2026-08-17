import unittest

import httpx

from app.config import settings
from app.external_feeds.collector import NvoidsCollector


class _FakeClient:
    def __init__(self, events: list[object], captured_kwargs: dict[str, object]):
        self._events = events
        self._captured_kwargs = captured_kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str):
        self._captured_kwargs["requested_url"] = url
        event = self._events.pop(0)
        if isinstance(event, Exception):
            raise event
        return event


class ExternalFeedsCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_connect_timeout = settings.nvoids_detail_connect_timeout_seconds
        self._original_read_timeout = settings.nvoids_detail_read_timeout_seconds
        self._original_write_timeout = settings.nvoids_detail_write_timeout_seconds
        self._original_pool_timeout = settings.nvoids_detail_pool_timeout_seconds
        self._original_retry_attempts = settings.nvoids_detail_retry_attempts
        self._original_retry_backoff_seconds = settings.nvoids_detail_retry_backoff_seconds

        settings.nvoids_detail_connect_timeout_seconds = 10.0
        settings.nvoids_detail_read_timeout_seconds = 45.0
        settings.nvoids_detail_write_timeout_seconds = 10.0
        settings.nvoids_detail_pool_timeout_seconds = 10.0
        settings.nvoids_detail_retry_attempts = 3
        settings.nvoids_detail_retry_backoff_seconds = "2,5,10"

    def tearDown(self) -> None:
        settings.nvoids_detail_connect_timeout_seconds = self._original_connect_timeout
        settings.nvoids_detail_read_timeout_seconds = self._original_read_timeout
        settings.nvoids_detail_write_timeout_seconds = self._original_write_timeout
        settings.nvoids_detail_pool_timeout_seconds = self._original_pool_timeout
        settings.nvoids_detail_retry_attempts = self._original_retry_attempts
        settings.nvoids_detail_retry_backoff_seconds = self._original_retry_backoff_seconds

    def test_detail_fetch_uses_browser_like_headers_and_extended_timeout(self) -> None:
        request = httpx.Request("GET", "https://nvoids.com/job_details.jsp?id=1")
        response = httpx.Response(200, request=request, text="<html>ok</html>")
        events = [response]
        captured: dict[str, object] = {}

        def _client_factory(**kwargs):
            captured.update(kwargs)
            return _FakeClient(events, captured)

        collector = NvoidsCollector(client_factory=_client_factory, sleep_func=lambda _seconds: None)
        page = collector.fetch_detail_page(url="https://nvoids.com/job_details.jsp?id=1")

        self.assertEqual(page.url, "https://nvoids.com/job_details.jsp?id=1")
        self.assertEqual(page.html, "<html>ok</html>")
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        self.assertEqual(timeout.connect, 10.0)
        self.assertEqual(timeout.read, 45.0)
        self.assertEqual(timeout.write, 10.0)
        self.assertEqual(timeout.pool, 10.0)
        headers = captured["headers"]
        assert isinstance(headers, dict)
        self.assertEqual(headers["Referer"], "https://nvoids.com/search_sph.jsp")
        self.assertEqual(headers["Accept-Language"], "en-US,en;q=0.9")
        self.assertEqual(headers["Cache-Control"], "no-cache")

    def test_detail_fetch_retries_once_then_succeeds(self) -> None:
        request = httpx.Request("GET", "https://nvoids.com/job_details.jsp?id=2")
        response = httpx.Response(200, request=request, text="<html>retry-ok</html>")
        events: list[object] = [httpx.ReadTimeout("The read operation timed out"), response]
        captured_calls: list[dict[str, object]] = []
        slept: list[float] = []

        def _client_factory(**kwargs):
            captured_calls.append(kwargs)
            return _FakeClient(events, {})

        collector = NvoidsCollector(client_factory=_client_factory, sleep_func=lambda seconds: slept.append(seconds))
        page = collector.fetch_detail_page(url="https://nvoids.com/job_details.jsp?id=2")

        self.assertEqual(page.html, "<html>retry-ok</html>")
        self.assertEqual(len(captured_calls), 2)
        self.assertEqual(slept, [2.0])
        self.assertEqual(collector.get_detail_fetch_metrics()["retry_count"], 1)
        self.assertEqual(collector.get_detail_fetch_metrics()["failure_count"], 0)

    def test_detail_fetch_exhausts_retries_for_read_timeout(self) -> None:
        events: list[object] = [
            httpx.ReadTimeout("timeout-1"),
            httpx.ReadTimeout("timeout-2"),
            httpx.ReadTimeout("timeout-3"),
        ]
        slept: list[float] = []

        def _client_factory(**kwargs):
            return _FakeClient(events, {})

        collector = NvoidsCollector(client_factory=_client_factory, sleep_func=lambda seconds: slept.append(seconds))

        with self.assertRaises(httpx.ReadTimeout):
            collector.fetch_detail_page(url="https://nvoids.com/job_details.jsp?id=3")

        self.assertEqual(slept, [2.0, 5.0])
        self.assertEqual(collector.get_detail_fetch_metrics()["retry_count"], 2)
        self.assertEqual(collector.get_detail_fetch_metrics()["failure_count"], 1)

    def test_detail_fetch_does_not_retry_http_status_error(self) -> None:
        request = httpx.Request("GET", "https://nvoids.com/job_details.jsp?id=4")
        response = httpx.Response(503, request=request)
        events: list[object] = [httpx.HTTPStatusError("service unavailable", request=request, response=response)]
        slept: list[float] = []
        call_count = 0

        def _client_factory(**kwargs):
            nonlocal call_count
            call_count += 1
            return _FakeClient(events, {})

        collector = NvoidsCollector(client_factory=_client_factory, sleep_func=lambda seconds: slept.append(seconds))

        with self.assertRaises(httpx.HTTPStatusError):
            collector.fetch_detail_page(url="https://nvoids.com/job_details.jsp?id=4")

        self.assertEqual(call_count, 1)
        self.assertEqual(slept, [])
        self.assertEqual(collector.get_detail_fetch_metrics()["retry_count"], 0)
        self.assertEqual(collector.get_detail_fetch_metrics()["failure_count"], 1)
