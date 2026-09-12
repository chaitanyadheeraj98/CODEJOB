"""Export one owner's approved taxonomy as versioned, reviewable repo data."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.job_intent_learning import normalize_job_intent_phrase
from app.models import CanonicalEntityTaxonomyEntry, JobIntentTaxonomyEntry
from app.skill_taxonomy import normalize_taxonomy_text


ENTITY_FILES = {
    "role": "roles.json",
    "company": "companies.json",
    "location": "locations.json",
}
CONTACT_IDENTIFIER_RE = re.compile(
    r"(?:\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|https?://|\bwww\.)",
    re.IGNORECASE,
)


def _contains_contact_identifier(value: str) -> bool:
    return bool(CONTACT_IDENTIFIER_RE.search(value))


def _aliases(value: str | None, canonical: str) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    aliases: list[str] = []
    seen = {normalize_taxonomy_text(canonical)}
    for item in parsed if isinstance(parsed, list) else []:
        alias = str(item).strip()
        normalized = normalize_taxonomy_text(alias)
        if (
            alias
            and normalized
            and normalized not in seen
            and not _contains_contact_identifier(alias)
        ):
            seen.add(normalized)
            aliases.append(alias)
    return sorted(aliases, key=str.casefold)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_base_taxonomy(db: Session, *, owner_id: str, output_dir: Path) -> dict[str, int]:
    owner_id = owner_id.strip()
    if not owner_id:
        raise ValueError("owner_id is required")
    output_dir.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {}
    for entity_type, file_name in ENTITY_FILES.items():
        rows = (
            db.query(
                CanonicalEntityTaxonomyEntry.canonical_name,
                CanonicalEntityTaxonomyEntry.aliases_json,
            )
            .filter(
                CanonicalEntityTaxonomyEntry.owner_id == owner_id,
                CanonicalEntityTaxonomyEntry.entity_type == entity_type,
                CanonicalEntityTaxonomyEntry.status == "approved",
            )
            .all()
        )
        entries = [
            {
                "aliases": _aliases(aliases_json, canonical_name),
                "canonical_name": canonical_name.strip(),
                "entity_type": entity_type,
            }
            for canonical_name, aliases_json in rows
            if canonical_name and canonical_name.strip()
            if not _contains_contact_identifier(canonical_name)
        ]
        entries.sort(
            key=lambda item: (
                normalize_taxonomy_text(str(item["canonical_name"])),
                str(item["canonical_name"]),
            )
        )
        _write_json(
            output_dir / file_name,
            {"schema_version": 1, "entity_type": entity_type, "entries": entries},
        )
        counts[entity_type] = len(entries)

    intent_rows = (
        db.query(
            JobIntentTaxonomyEntry.phrase,
            JobIntentTaxonomyEntry.polarity,
            JobIntentTaxonomyEntry.confidence_aggregate,
        )
        .filter(
            JobIntentTaxonomyEntry.owner_id == owner_id,
            JobIntentTaxonomyEntry.status == "approved",
        )
        .all()
    )
    intents = [
        {
            "confidence": max(0.0, min(float(confidence or 0.0), 1.0)),
            "normalized_phrase": normalize_job_intent_phrase(phrase),
            "phrase": phrase.strip(),
            "polarity": polarity.strip(),
        }
        for phrase, polarity, confidence in intent_rows
        if phrase and phrase.strip() and polarity and polarity.strip()
        if not _contains_contact_identifier(phrase)
    ]
    intents.sort(
        key=lambda item: (
            str(item["polarity"]),
            str(item["normalized_phrase"]),
            str(item["phrase"]),
        )
    )
    _write_json(output_dir / "job_intent.json", {"schema_version": 1, "entries": intents})
    counts["job_intent"] = len(intents)

    version_path = output_dir / "VERSION"
    try:
        version = int(version_path.read_text(encoding="utf-8").strip()) + 1
    except (FileNotFoundError, ValueError):
        version = 1
    version_path.write_text(f"{version}\n", encoding="utf-8")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True, help="Owner whose approved rows become the base")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parents[1] / "app" / "data" / "base_taxonomy",
    )
    args = parser.parse_args()
    with SessionLocal() as db:
        counts = export_base_taxonomy(db, owner_id=args.owner_id, output_dir=args.output_dir)
    print(json.dumps({"version": int((args.output_dir / "VERSION").read_text()), "counts": counts}, sort_keys=True))


if __name__ == "__main__":
    main()
