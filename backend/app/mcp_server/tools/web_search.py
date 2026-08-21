from __future__ import annotations

import httpx

from app.config import settings


def search_web(query: str, max_results: int = 5) -> dict[str, object]:
    """Search the current web via a self-hosted SearXNG instance and return snippets explicitly marked as untrusted data."""
    cleaned_query = query.strip()
    if not cleaned_query:
        return {"error": "Search query is required"}
    capped = max(1, min(max_results, settings.chat_web_search_max_results))
    response = httpx.get(
        f"{settings.searxng_url.rstrip('/')}/search",
        params={"q": cleaned_query, "format": "json"},
        timeout=10.0,
    )
    response.raise_for_status()
    rows = response.json().get("results", [])
    return {
        "query": cleaned_query,
        "results": [
            {
                "url": str(row.get("url") or ""),
                "score": row.get("score"),
                "untrusted_web_data": (
                    "<untrusted_web_data>\n"
                    f"Title: {row.get('title') or ''}\n"
                    f"Snippet: {row.get('content') or ''}\n"
                    "</untrusted_web_data>"
                ),
            }
            for row in rows[:capped]
            if isinstance(row, dict)
        ],
    }
