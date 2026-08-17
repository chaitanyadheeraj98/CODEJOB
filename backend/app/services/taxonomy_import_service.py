from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models import CustomSkillTaxonomyEntry
from app.skill_taxonomy import SkillTaxonomy, clear_skill_taxonomy_cache, load_skill_taxonomy, normalize_taxonomy_text


@dataclass(frozen=True)
class TaxonomyImportCandidate:
    canonical_name: str
    aliases: tuple[str, ...]
    category: str
    description: str


@dataclass(frozen=True)
class TaxonomyImportCollision:
    canonical_name: str
    alias: str
    existing_owner: str


def _records(payload: object) -> Iterable[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        skills = payload.get("skills")
        if isinstance(skills, list):
            return skills
        return payload.values()
    return ()


def load_import_candidates(path: Path) -> list[TaxonomyImportCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates: list[TaxonomyImportCandidate] = []
    for item in _records(payload):
        if not isinstance(item, dict):
            continue
        canonical_name = str(
            item.get("canonical_name") or item.get("skill_name") or item.get("name") or ""
        ).strip()
        if not normalize_taxonomy_text(canonical_name):
            continue
        raw_aliases = item.get("aliases") or item.get("normalized_forms") or []
        if isinstance(raw_aliases, str):
            raw_aliases = [raw_aliases]
        aliases = tuple(
            dict.fromkeys(
                str(alias).strip()
                for alias in raw_aliases
                if str(alias).strip() and normalize_taxonomy_text(str(alias)) != normalize_taxonomy_text(canonical_name)
            )
        ) if isinstance(raw_aliases, list) else ()
        candidates.append(
            TaxonomyImportCandidate(
                canonical_name=canonical_name,
                aliases=aliases,
                category=str(item.get("category") or item.get("type") or "custom").strip() or "custom",
                description=str(item.get("description") or "").strip(),
            )
        )
    return candidates


def analyze_import(
    candidates: Iterable[TaxonomyImportCandidate],
    *,
    taxonomy: SkillTaxonomy | None = None,
) -> tuple[list[TaxonomyImportCandidate], list[TaxonomyImportCollision]]:
    current = taxonomy or load_skill_taxonomy()
    accepted: list[TaxonomyImportCandidate] = []
    collisions: list[TaxonomyImportCollision] = []
    claimed: dict[str, str] = {
        alias: normalize_taxonomy_text(entry.canonical_name)
        for alias, entry in current.exact_lookup.items()
    }
    seen_canonicals: set[str] = set()
    for candidate in candidates:
        canonical_key = normalize_taxonomy_text(candidate.canonical_name)
        if canonical_key in seen_canonicals:
            collisions.append(
                TaxonomyImportCollision(candidate.canonical_name, candidate.canonical_name, "duplicate incoming canonical")
            )
            continue
        seen_canonicals.add(canonical_key)
        candidate_collisions: list[TaxonomyImportCollision] = []
        for raw_alias in (candidate.canonical_name, *candidate.aliases):
            alias = normalize_taxonomy_text(raw_alias)
            existing_owner = claimed.get(alias)
            if existing_owner is not None and existing_owner != canonical_key:
                candidate_collisions.append(
                    TaxonomyImportCollision(candidate.canonical_name, raw_alias, existing_owner)
                )
        if candidate_collisions:
            collisions.extend(candidate_collisions)
            continue
        accepted.append(candidate)
        for raw_alias in (candidate.canonical_name, *candidate.aliases):
            claimed[normalize_taxonomy_text(raw_alias)] = canonical_key
    return accepted, collisions


def apply_import(
    db: Session,
    candidates: Iterable[TaxonomyImportCandidate],
    *,
    owner_id: str,
) -> int:
    rows = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(CustomSkillTaxonomyEntry.owner_id == owner_id)
        .all()
    )
    existing = {normalize_taxonomy_text(row.canonical_name): row for row in rows}
    changed = 0
    for candidate in candidates:
        key = normalize_taxonomy_text(candidate.canonical_name)
        row = existing.get(key)
        if row is None:
            row = CustomSkillTaxonomyEntry(owner_id=owner_id, canonical_name=candidate.canonical_name)
            db.add(row)
            existing[key] = row
        row.canonical_name = candidate.canonical_name
        row.aliases_json = json.dumps(list(candidate.aliases), separators=(",", ":"))
        row.category = candidate.category
        row.description = candidate.description
        row.status = "approved"
        row.embedding_status = "pending"
        changed += 1
    if changed:
        db.commit()
        clear_skill_taxonomy_cache()
    return changed
