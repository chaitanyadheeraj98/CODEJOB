from __future__ import annotations

QUERY_BUCKET_LIMIT = 10


def _normalize_query(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def sanitize_saved_queries(values: object, limit: int = QUERY_BUCKET_LIMIT) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        normalized = _normalize_query(raw)
        if not normalized:
            continue
        dedupe_key = normalized.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(normalized)
        if len(cleaned) >= limit:
            break
    return cleaned
