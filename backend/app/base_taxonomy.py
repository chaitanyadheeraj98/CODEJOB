"""Read the reviewed, versioned taxonomy bundled with the application."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import settings


BASE_TAXONOMY_DIR = Path(__file__).parent / "data" / "base_taxonomy"
ENTITY_FILES = {
    "role": "roles.json",
    "company": "companies.json",
    "location": "locations.json",
}


@dataclass(frozen=True)
class BaseEntityEntry:
    entity_type: str
    canonical_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class BaseJobIntentEntry:
    phrase: str
    normalized_phrase: str
    polarity: str
    confidence: float


def _payload(file_name: str) -> dict[str, object]:
    path = BASE_TAXONOMY_DIR / file_name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid base taxonomy artifact: {path}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise RuntimeError(f"Unsupported base taxonomy schema: {path}")
    return payload


@lru_cache(maxsize=3)
def load_base_entity_entries(entity_type: str) -> tuple[BaseEntityEntry, ...]:
    file_name = ENTITY_FILES.get(entity_type)
    if file_name is None:
        return ()
    payload = _payload(file_name)
    if payload.get("entity_type") != entity_type or not isinstance(payload.get("entries"), list):
        raise RuntimeError(f"Invalid base taxonomy entries: {file_name}")

    entries: list[BaseEntityEntry] = []
    for item in payload["entries"]:
        if not isinstance(item, dict) or item.get("entity_type") != entity_type:
            raise RuntimeError(f"Invalid base taxonomy entry: {file_name}")
        canonical = str(item.get("canonical_name") or "").strip()
        aliases = item.get("aliases")
        if not canonical or not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise RuntimeError(f"Invalid base taxonomy entry: {file_name}")
        entries.append(
            BaseEntityEntry(
                entity_type=entity_type,
                canonical_name=canonical,
                aliases=tuple(alias.strip() for alias in aliases if alias.strip()),
            )
        )
    return tuple(entries)


@lru_cache(maxsize=1)
def load_base_job_intent_entries() -> tuple[BaseJobIntentEntry, ...]:
    file_name = "job_intent.json"
    payload = _payload(file_name)
    if not isinstance(payload.get("entries"), list):
        raise RuntimeError(f"Invalid base taxonomy entries: {file_name}")

    entries: list[BaseJobIntentEntry] = []
    for item in payload["entries"]:
        if not isinstance(item, dict):
            raise RuntimeError(f"Invalid base taxonomy entry: {file_name}")
        phrase = str(item.get("phrase") or "").strip()
        normalized = str(item.get("normalized_phrase") or "").strip()
        polarity = str(item.get("polarity") or "").strip()
        confidence = item.get("confidence")
        if not phrase or not normalized or not polarity or not isinstance(confidence, (int, float)):
            raise RuntimeError(f"Invalid base taxonomy entry: {file_name}")
        entries.append(
            BaseJobIntentEntry(
                phrase=phrase,
                normalized_phrase=normalized,
                polarity=polarity,
                confidence=max(0.0, min(float(confidence), 1.0)),
            )
        )
    return tuple(entries)


def base_entity_entries(entity_type: str) -> tuple[BaseEntityEntry, ...]:
    """The base layer for `entity_type`, or nothing when it is switched off.

    The flag is checked here rather than inside the cached loader, so the cache
    stays keyed on the artefact alone and cannot serve a stale answer across a
    flag change.
    """
    if not settings.feature_base_taxonomy_enabled:
        return ()
    return load_base_entity_entries(entity_type)


def base_job_intent_entries() -> tuple[BaseJobIntentEntry, ...]:
    if not settings.feature_base_taxonomy_enabled:
        return ()
    return load_base_job_intent_entries()


def clear_base_taxonomy_cache() -> None:
    load_base_entity_entries.cache_clear()
    load_base_job_intent_entries.cache_clear()
