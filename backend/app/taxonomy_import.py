from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.db import SessionLocal
from app.services.taxonomy_import_service import analyze_import, apply_import, load_import_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze or import a pinned master-skill-taxonomy JSON file.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write collision-free entries to the DB; default is dry-run.")
    parser.add_argument("--owner-id", default="default-owner")
    parser.add_argument("--collisions-output", type=Path)
    args = parser.parse_args()

    candidates = load_import_candidates(args.source)
    accepted, collisions = analyze_import(candidates)
    collision_payload = [collision.__dict__ for collision in collisions]
    if args.collisions_output:
        args.collisions_output.write_text(json.dumps(collision_payload, indent=2), encoding="utf-8")
    imported_count = 0
    if args.apply:
        with SessionLocal() as db:
            imported_count = apply_import(db, accepted, owner_id=args.owner_id)
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "source_count": len(candidates),
                "accepted_count": len(accepted),
                "collision_count": len(collisions),
                "imported_count": imported_count,
                "collisions": collision_payload,
            },
            indent=2,
        )
    )
    return 0 if not collisions else 2


if __name__ == "__main__":
    raise SystemExit(main())
