from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import time
from urllib.parse import urlencode

import httpx

from app.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectedPage:
    url: str
    html: str


class NvoidsCollector:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        client_factory: Callable[..., httpx.Client] | None = None,
        sleep_func: Callable[[float], None] | None = None,
    ):
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or httpx.Client
        self._sleep = sleep_func or time.sleep
        self._detail_fetch_retry_count = 0
        self._detail_fetch_failure_count = 0

    def reset_detail_fetch_metrics(self) -> None:
        self._detail_fetch_retry_count = 0
        self._detail_fetch_failure_count = 0

    def get_detail_fetch_metrics(self) -> dict[str, int]:
        return {
            "retry_count": self._detail_fetch_retry_count,
            "failure_count": self._detail_fetch_failure_count,
        }

    @staticmethod
    def _base_headers() -> dict[str, str]:
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }

    @staticmethod
    def _detail_headers() -> dict[str, str]:
        return {
            **NvoidsCollector._base_headers(),
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://nvoids.com/search_sph.jsp",
            "Cache-Control": "no-cache",
        }

    @staticmethod
    def _detail_timeout() -> httpx.Timeout:
        return httpx.Timeout(
            connect=settings.nvoids_detail_connect_timeout_seconds,
            read=settings.nvoids_detail_read_timeout_seconds,
            write=settings.nvoids_detail_write_timeout_seconds,
            pool=settings.nvoids_detail_pool_timeout_seconds,
        )

    @staticmethod
    def _detail_retry_backoffs() -> list[float]:
        values: list[float] = []
        for raw in str(settings.nvoids_detail_retry_backoff_seconds or "").split(","):
            token = raw.strip()
            if not token:
                continue
            try:
                values.append(max(0.0, float(token)))
            except ValueError:
                continue
        return values

    @staticmethod
    def _is_retriable_detail_error(exc: Exception) -> bool:
        return isinstance(exc, (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError))

    def fetch_page(self, base_url: str, page: int) -> CollectedPage:
        params = {"p": page} if page > 0 else {}
        query = f"?{urlencode(params)}" if params else ""
        page_url = base_url.rstrip("/") + "/index.jsp" + query if "index.jsp" not in base_url else base_url + query
        with self._client_factory(timeout=self.timeout_seconds, follow_redirects=True, headers=self._base_headers()) as client:
            res = client.get(page_url)
            res.raise_for_status()
            return CollectedPage(url=page_url, html=res.text)

    def fetch_search_page(self, *, query: str, hotlist_mode: str = "Exclude Hotlists", page: int = 0) -> CollectedPage:
        params = {
            "searchstring": query,
            "sel": hotlist_mode,
            "p": str(page),
        }
        with self._client_factory(timeout=self.timeout_seconds, follow_redirects=True, headers=self._base_headers()) as client:
            logger.info(
                "nvoids_fetch_search_start page=%s timeout_seconds=%.1f query=%r hotlist_mode=%r",
                page,
                self.timeout_seconds,
                query,
                hotlist_mode,
            )
            try:
                res = client.get("https://nvoids.com/search_sph.jsp", params=params)
            except Exception:
                logger.exception(
                    "nvoids_fetch_search_failed page=%s timeout_seconds=%.1f query=%r",
                    page,
                    self.timeout_seconds,
                    query,
                )
                raise
            res.raise_for_status()
            logger.info(
                "nvoids_fetch_search_ok page=%s final_url=%r status_code=%s chars=%s",
                page,
                str(res.url),
                res.status_code,
                len(res.text or ""),
            )
            return CollectedPage(url=str(res.url), html=res.text)

    def fetch_detail_page(self, *, url: str) -> CollectedPage:
        headers = self._detail_headers()
        timeout = self._detail_timeout()
        max_attempts = max(1, int(settings.nvoids_detail_retry_attempts or 1))
        retry_backoffs = self._detail_retry_backoffs()
        last_exception: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()
            logger.info(
                "nvoids_fetch_detail_start attempt=%s/%s connect_timeout=%.1f read_timeout=%.1f write_timeout=%.1f pool_timeout=%.1f url=%r",
                attempt,
                max_attempts,
                settings.nvoids_detail_connect_timeout_seconds,
                settings.nvoids_detail_read_timeout_seconds,
                settings.nvoids_detail_write_timeout_seconds,
                settings.nvoids_detail_pool_timeout_seconds,
                url,
            )
            try:
                with self._client_factory(timeout=timeout, follow_redirects=True, headers=headers) as client:
                    res = client.get(url)
                    res.raise_for_status()
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                logger.info(
                    "nvoids_fetch_detail_ok attempt=%s/%s url=%r final_url=%r status_code=%s chars=%s elapsed_ms=%.2f",
                    attempt,
                    max_attempts,
                    url,
                    str(res.url),
                    res.status_code,
                    len(res.text or ""),
                    elapsed_ms,
                )
                return CollectedPage(url=str(res.url), html=res.text)
            except Exception as exc:
                elapsed_ms = (time.perf_counter() - started_at) * 1000
                last_exception = exc
                will_retry = attempt < max_attempts and self._is_retriable_detail_error(exc)
                logger.warning(
                    "nvoids_fetch_detail_attempt_failed attempt=%s/%s url=%r error_type=%s elapsed_ms=%.2f will_retry=%s error=%s",
                    attempt,
                    max_attempts,
                    url,
                    type(exc).__name__,
                    elapsed_ms,
                    will_retry,
                    exc,
                )
                if not will_retry:
                    self._detail_fetch_failure_count += 1
                    logger.exception(
                        "nvoids_fetch_detail_failed timeout_read_seconds=%.1f attempts=%s url=%r",
                        settings.nvoids_detail_read_timeout_seconds,
                        attempt,
                        url,
                    )
                    raise
                self._detail_fetch_retry_count += 1
                backoff_index = attempt - 1
                backoff_seconds = retry_backoffs[backoff_index] if backoff_index < len(retry_backoffs) else 0.0
                if backoff_seconds > 0:
                    self._sleep(backoff_seconds)

        self._detail_fetch_failure_count += 1
        logger.exception(
            "nvoids_fetch_detail_failed timeout_read_seconds=%.1f attempts=%s url=%r",
            settings.nvoids_detail_read_timeout_seconds,
            max_attempts,
            url,
        )
        if last_exception is not None:
            raise last_exception
        raise RuntimeError(f"nvoids_detail_fetch_failed_without_exception: {url}")
