from __future__ import annotations

from dataclasses import dataclass
import logging
from urllib.parse import urlencode

import httpx


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectedPage:
    url: str
    html: str


class NvoidsCollector:
    def __init__(self, *, timeout_seconds: float = 20.0):
        self.timeout_seconds = timeout_seconds

    def fetch_page(self, base_url: str, page: int) -> CollectedPage:
        params = {"p": page} if page > 0 else {}
        query = f"?{urlencode(params)}" if params else ""
        page_url = base_url.rstrip("/") + "/index.jsp" + query if "index.jsp" not in base_url else base_url + query
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            res = client.get(page_url)
            res.raise_for_status()
            return CollectedPage(url=page_url, html=res.text)

    def fetch_search_page(self, *, query: str, hotlist_mode: str = "Exclude Hotlists", page: int = 0) -> CollectedPage:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }
        params = {
            "searchstring": query,
            "sel": hotlist_mode,
            "p": str(page),
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            # nvoids accepts query via both landing and search page flows; send direct search endpoint.
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
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            logger.info("nvoids_fetch_detail_start timeout_seconds=%.1f url=%r", self.timeout_seconds, url)
            try:
                res = client.get(url)
            except Exception:
                logger.exception("nvoids_fetch_detail_failed timeout_seconds=%.1f url=%r", self.timeout_seconds, url)
                raise
            res.raise_for_status()
            logger.info(
                "nvoids_fetch_detail_ok url=%r final_url=%r status_code=%s chars=%s",
                url,
                str(res.url),
                res.status_code,
                len(res.text or ""),
            )
            return CollectedPage(url=str(res.url), html=res.text)
