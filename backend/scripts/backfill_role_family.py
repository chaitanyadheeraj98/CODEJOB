"""Populate role_family / role_family_confidence / role_family_taxonomy_version
on rows that predate migration 0061.

Deliberately not part of the migration: docker-compose runs `alembic upgrade head`
on backend boot, so anything that scans recruiter_emails there is a boot-time
outage risk. This runs on demand instead.

Every row is classified from its own stored `role` and `skills_text`, which is the
same input the write sites now use at row construction - so a backfilled row and a
freshly written one carry identical values.

Re-running changes nothing: rows are selected only when role_family IS NULL, or
when --stale-version is passed and the stored taxonomy version is not the current
one. That second mode is how a ROLE_FAMILY_TAXONOMY_VERSION bump gets re-driven
across the table.

Usage:
    python scripts/backfill_role_family.py --dry-run
    python scripts/backfill_role_family.py --batch-size 500
    python scripts/backfill_role_family.py --stale-version   # after a taxonomy bump
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import or_  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import RecruiterEmail  # noqa: E402
from app.services.role_provenance import role_family_fields  # noqa: E402
from app.skill_taxonomy import ROLE_FAMILY_TAXONOMY_VERSION  # noqa: E402


def _confidence_bucket(confidence: float) -> str:
    """Coarse bands, because the interesting question is "how many rows are we
    not entitled to trust", not the exact score."""
    if confidence >= 0.9:
        return "0.90+"
    if confidence >= 0.7:
        return "0.70-0.89"
    if confidence > 0.0:
        return "0.01-0.69"
    return "0.00 (unclassified)"


def backfill(*, batch_size: int, dry_run: bool, stale_version: bool) -> dict[str, object]:
    families: Counter[str] = Counter()
    confidences: Counter[str] = Counter()
    updated = 0
    last_id = 0

    with SessionLocal() as db:
        while True:
            query = db.query(RecruiterEmail).filter(RecruiterEmail.id > last_id)
            if stale_version:
                query = query.filter(
                    or_(
                        RecruiterEmail.role_family.is_(None),
                        RecruiterEmail.role_family_taxonomy_version
                        != ROLE_FAMILY_TAXONOMY_VERSION,
                    )
                )
            else:
                query = query.filter(RecruiterEmail.role_family.is_(None))
            # Keyset, not OFFSET: without --dry-run each batch stops matching the
            # filter once committed, so an offset would skip rows.
            rows = query.order_by(RecruiterEmail.id).limit(batch_size).all()
            if not rows:
                break

            for email in rows:
                fields = role_family_fields(role=email.role, skills_text=email.skills_text)
                families[str(fields["role_family"])] += 1
                confidences[_confidence_bucket(float(fields["role_family_confidence"]))] += 1
                if not dry_run:
                    for column, value in fields.items():
                        setattr(email, column, value)
                updated += 1

            last_id = rows[-1].id
            if dry_run:
                db.expunge_all()
            else:
                db.commit()

    return {"updated": updated, "families": families, "confidences": confidences}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="Report the histogram without writing")
    parser.add_argument(
        "--stale-version",
        action="store_true",
        help=f"Also reclassify rows not on taxonomy version {ROLE_FAMILY_TAXONOMY_VERSION}",
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    stats = backfill(
        batch_size=args.batch_size, dry_run=args.dry_run, stale_version=args.stale_version
    )
    mode = "DRY RUN - " if args.dry_run else ""
    print(f"{mode}Classified {stats['updated']} emails as {ROLE_FAMILY_TAXONOMY_VERSION}")
    for family, count in stats["families"].most_common():
        print(f"  family {family:<16} {count}")
    for bucket, count in sorted(stats["confidences"].items(), reverse=True):
        print(f"  confidence {bucket:<20} {count}")


if __name__ == "__main__":
    main()
