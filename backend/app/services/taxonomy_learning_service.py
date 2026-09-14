from __future__ import annotations

import json
import math
import re
from time import perf_counter
from typing import Any

from sqlalchemy.orm import Session

from app.base_taxonomy import base_entity_entries
from app.models import CanonicalEntityTaxonomyEntry, CustomSkillTaxonomyEntry, RecruiterEmail
from app.semantic.embeddings_service import embedding_to_json, generate_embeddings
from app.skill_taxonomy import (
    TAXONOMY_PLACEHOLDER_KEYS,
    clear_skill_taxonomy_cache,
    load_skill_taxonomy,
    normalize_taxonomy_text,
)


# "role" joins company/location so job titles reuse the same learn -> review ->
# approve loop rather than getting a parallel mechanism. Seeded role entries are
# written as "pending": nothing enters the trusted vocabulary without a human.
ENTITY_TYPES = {"company", "location", "role"}
BULK_APPROVAL_MIN_OCCURRENCES = 2


def _json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_list(value: str | None) -> list[object]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _clean_entity_name(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _valid_entity_name(value: str) -> bool:
    normalized = normalize_taxonomy_text(value)
    if not normalized or normalized in TAXONOMY_PLACEHOLDER_KEYS | {"none"}:
        return False
    return "\n" not in value and len(value) <= 160 and len(value.split()) <= 16


def is_safe_for_bulk_entity_approval(value: str) -> bool:
    if not _valid_entity_name(value):
        return False
    if any(value.count(opening) != value.count(closing) for opening, closing in (("(", ")"), ("[", "]"))):
        return False
    return not bool(re.search(r"[.!?]\s+[A-Z]", value))


def _entity_values(parser_details_json: str | None, entity_type: str) -> list[str]:
    payload = _json_object(parser_details_json)
    ai_result = payload.get("ai_extractor_result")
    if not isinstance(ai_result, dict):
        return []
    if entity_type == "company":
        values: list[object] = [ai_result.get("company")]
    elif entity_type == "role":
        # role_candidates is the extractor's own shortlist of titles for the JD and is
        # populated on 4,610 rows; `role` is its single chosen answer. Harvesting both
        # gives aliases for free ("Sr. Java Developer" alongside "Java Developer").
        values = [ai_result.get("role")]
        candidates = ai_result.get("role_candidates")
        if isinstance(candidates, list):
            values.extend(candidates)
    else:
        values = [ai_result.get("primary_location")]
        mentioned = ai_result.get("mentioned_locations")
        if isinstance(mentioned, list):
            values.extend(mentioned)
    return [cleaned for item in values if _valid_entity_name(cleaned := _clean_entity_name(item))]


def _suppressed_entity_keys(db: Session, *, owner_id: str, entity_type: str) -> set[str]:
    suppressed = {
        key
        for entry in base_entity_entries(entity_type)
        for value in (entry.canonical_name, *entry.aliases)
        if (key := normalize_taxonomy_text(value))
    }
    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
            CanonicalEntityTaxonomyEntry.status.in_(("approved", "dismissed")),
        )
        .all()
    )
    for row in rows:
        values = [row.canonical_name]
        values.extend(str(item) for item in _json_list(row.aliases_json))
        suppressed.update(key for item in values if (key := normalize_taxonomy_text(item)))
    return suppressed


def list_pending_entities(db: Session, *, owner_id: str, entity_type: str) -> list[dict[str, object]]:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    suppressed = _suppressed_entity_keys(db, owner_id=owner_id, entity_type=entity_type)
    aggregated: dict[str, dict[str, object]] = {}
    rows = (
        db.query(RecruiterEmail.id, RecruiterEmail.parser_details_json)
        .filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.parser_details_json.is_not(None),
        )
        .order_by(RecruiterEmail.id.desc())
        .all()
    )
    for email_id, parser_details_json in rows:
        seen: set[str] = set()
        for display_name in _entity_values(parser_details_json, entity_type):
            normalized = normalize_taxonomy_text(display_name)
            if not normalized or normalized in suppressed or normalized in seen:
                continue
            seen.add(normalized)
            bucket = aggregated.setdefault(
                normalized,
                {
                    "entity_type": entity_type,
                    "display_name": display_name,
                    "normalized_name": normalized,
                    "occurrence_count": 0,
                    "candidate_ids": [],
                },
            )
            bucket["occurrence_count"] = int(bucket["occurrence_count"]) + 1
            candidate_ids = bucket["candidate_ids"]
            if isinstance(candidate_ids, list):
                candidate_ids.append(int(email_id))
    results = list(aggregated.values())
    for item in results:
        candidate_ids = item["candidate_ids"] if isinstance(item["candidate_ids"], list) else []
        item["candidate_ids"] = sorted({int(candidate_id) for candidate_id in candidate_ids}, reverse=True)
    results.sort(key=lambda item: (-int(item["occurrence_count"]), str(item["display_name"]).casefold()))
    return results


def upsert_entity(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    display_name: str,
    canonical_name: str | None,
    aliases: list[str],
    occurrence_count: int,
    status: str,
    auto_commit: bool = True,
) -> CanonicalEntityTaxonomyEntry:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"entity_type must be one of {sorted(ENTITY_TYPES)}")
    effective_name = _clean_entity_name(canonical_name or display_name)
    if not _valid_entity_name(effective_name):
        raise ValueError("A valid canonical entity name is required")
    normalized = normalize_taxonomy_text(effective_name)
    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
        )
        .order_by(CanonicalEntityTaxonomyEntry.id.asc())
        .all()
    )
    cleaned_aliases = []
    seen_aliases = {normalized}
    for raw in [display_name, *aliases]:
        alias = _clean_entity_name(raw)
        key = normalize_taxonomy_text(alias)
        if alias and key and key not in seen_aliases:
            seen_aliases.add(key)
            cleaned_aliases.append(alias)
    row = next((item for item in rows if normalize_taxonomy_text(item.canonical_name) == normalized), None)
    if row is None:
        row = CanonicalEntityTaxonomyEntry(owner_id=owner_id, entity_type=entity_type, canonical_name=effective_name)
        db.add(row)
    row.canonical_name = effective_name
    row.aliases_json = json.dumps(cleaned_aliases, separators=(",", ":"))
    row.occurrence_count = max(int(row.occurrence_count or 0), max(0, occurrence_count))
    row.embedding_status = "pending"
    row.status = status
    row.suppressed = status == "dismissed"
    if auto_commit:
        db.commit()
        db.refresh(row)
    return row


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return -1.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(sum(value * value for value in right))
    return (sum(a * b for a, b in zip(left, right)) / denominator) if denominator else -1.0


def embed_pending_skills(db: Session, *, owner_id: str, batch_size: int = 300) -> dict[str, int]:
    started = perf_counter()
    rows = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == owner_id,
            CustomSkillTaxonomyEntry.status == "approved",
            CustomSkillTaxonomyEntry.embedding_status == "pending",
        )
        .order_by(CustomSkillTaxonomyEntry.occurrence_count.desc(), CustomSkillTaxonomyEntry.id.asc())
        .limit(max(1, min(batch_size, 500)))
        .all()
    )
    if not rows:
        return {"embedded_count": 0, "remaining_count": 0, "duration_ms": 0}

    references = [entry for entry in load_skill_taxonomy().entries if not entry.id.startswith("custom::")]
    row_texts = [f"{row.canonical_name}. {row.description or ''} Category: {row.category or 'custom'}." for row in rows]
    reference_texts = [f"{entry.canonical_name}. Category: {entry.category}." for entry in references]
    vectors, _provider = generate_embeddings([*reference_texts, *row_texts])
    reference_vectors = vectors[: len(references)]
    row_vectors = vectors[len(references) :]

    for row, vector in zip(rows, row_vectors):
        nearest = None
        nearest_score = -1.0
        for entry, reference_vector in zip(references, reference_vectors):
            score = _cosine(vector, reference_vector)
            if score > nearest_score:
                nearest = entry
                nearest_score = score
        row.embedding_json = embedding_to_json(vector)
        row.embedding_status = "done"
        if nearest is not None:
            row.category = nearest.category
            row.cluster_hint = nearest.cluster_id
            row.match_tier = nearest.match_tier
            row.weight = max(float(nearest.weight), 1.1 if row.occurrence_count >= 10 else 0.1)

    db.commit()
    clear_skill_taxonomy_cache()
    remaining = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == owner_id,
            CustomSkillTaxonomyEntry.status == "approved",
            CustomSkillTaxonomyEntry.embedding_status == "pending",
        )
        .count()
    )
    return {
        "embedded_count": len(rows),
        "remaining_count": remaining,
        "duration_ms": int((perf_counter() - started) * 1000),
    }
