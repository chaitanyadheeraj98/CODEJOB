from __future__ import annotations

import httpx

from app.mcp_server.tools import needs, untrusted
from app.config import settings

MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 300


def search_web(query: str, max_results: int = 5) -> dict[str, object]:
    """Search the current web via a self-hosted SearXNG instance and return snippets explicitly marked as untrusted data."""
    cleaned_query = query.strip()
    if not cleaned_query:
        return {**needs(["query"], hint="Ask the user what to search for."), "error": "Search query is required"}
    capped = max(1, min(max_results, settings.chat_web_search_max_results))
    response = httpx.get(
        f"{settings.searxng_url.rstrip('/')}/search",
        params={"q": cleaned_query, "format": "json"},
        timeout=10.0,
    )
    response.raise_for_status()
    rows = response.json().get("results", [])
    return {
        # The discriminator every other render payload carries, so the results
        # can be shown to the user instead of being dropped from the thread.
        "action": "search_web",
        "query": cleaned_query,
        "results": [
            {
                "url": str(row.get("url") or ""),
                "score": row.get("score"),
                # title and snippet are what the *component* renders; the
                # delimited string below is what the *model* reads. The
                # delimiters stay - splitting these out does not remove them
                # from the text the model consumes.
                "title": str(row.get("title") or "")[:MAX_TITLE_CHARS],
                "snippet": str(row.get("content") or "")[:MAX_SNIPPET_CHARS],
                "untrusted_web_data": (
                    untrusted("web",
                    f"Title: {row.get('title') or ''}\n"
                    f"Snippet: {row.get('content') or ''}")
                ),
            }
            for row in rows[:capped]
            if isinstance(row, dict)
        ],
    }
