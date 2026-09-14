from __future__ import annotations

import argparse
import json

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import CustomSkillTaxonomyEntry
from app.parsing.skill_audit import custom_skill_requires_review
from app.skill_taxonomy import clear_skill_taxonomy_cache
from app import tenancy


def flagged_approved_entries(db: Session, *, owner_id: str) -> list[CustomSkillTaxonomyEntry]:
    rows = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == owner_id,
            CustomSkillTaxonomyEntry.status == "approved",
        )
        .order_by(CustomSkillTaxonomyEntry.id.asc())
        .all()
    )
    return [row for row in rows if custom_skill_requires_review(row.canonical_name)]


def run_cleanup(
    *,
    apply_ids: set[int] | None = None,
    owner_id: str | None = None,
    limit: int = 100,
) -> dict[str, object]:
    effective_owner_id = owner_id or tenancy.owner_id()
    with SessionLocal() as db:
        flagged = flagged_approved_entries(db, owner_id=effective_owner_id)
        requested_ids = apply_ids or set()
        selected = [row for row in flagged if row.id in requested_ids]
        missing_ids = sorted(requested_ids - {row.id for row in selected})
        if missing_ids:
            raise ValueError(f"IDs are not approved flagged entries for this owner: {missing_ids}")
        rows = [
            {
                "id": row.id,
                "canonical_name": row.canonical_name,
                "embedding_status": row.embedding_status,
            }
            for row in (selected if requested_ids else flagged[: max(0, limit)])
        ]
        if requested_ids:
            for row in selected:
                row.status = "flagged_for_review"
            db.commit()
            clear_skill_taxonomy_cache()
        return {
            "mode": "apply" if requested_ids else "preview",
            "owner_id": effective_owner_id,
            "flagged_count": len(flagged),
            "applied_count": len(selected),
            "shown_count": len(rows),
            "rows": rows,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Preview suspicious approved custom skills; pass reviewed --apply-id values to demote only those rows."
    )
    parser.add_argument("--apply-id", action="append", type=int, default=[])
    parser.add_argument("--owner-id", default=None)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    print(
        json.dumps(
            run_cleanup(apply_ids=set(args.apply_id), owner_id=args.owner_id, limit=max(0, args.limit)),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
