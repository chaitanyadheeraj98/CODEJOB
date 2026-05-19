from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


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
            res = client.get("https://nvoids.com/search_sph.jsp", params=params)
            res.raise_for_status()
            return CollectedPage(url=str(res.url), html=res.text)

    def fetch_detail_page(self, *, url: str) -> CollectedPage:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "text/html,application/xhtml+xml",
        }
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            res = client.get(url)
            res.raise_for_status()
            return CollectedPage(url=str(res.url), html=res.text)
