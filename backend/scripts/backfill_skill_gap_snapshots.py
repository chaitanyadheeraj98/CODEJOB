"""Backfill frozen skill-gap snapshots after migration 0033.

Usage: python scripts/backfill_skill_gap_snapshots.py --batch-size 100
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Application, ApplicationSkillGapSnapshot  # noqa: E402
from app.services.resume_tracking_service import compute_skill_gap  # noqa: E402


def backfill(batch_size: int) -> int:
    completed = 0
    last_id = 0
    with SessionLocal() as db:
        while True:
            rows = (
                db.query(Application)
                .outerjoin(
                    ApplicationSkillGapSnapshot,
                    ApplicationSkillGapSnapshot.application_id == Application.id,
                )
                .filter(
                    Application.id > last_id,
                    Application.deleted_at.is_(None),
                    ApplicationSkillGapSnapshot.id.is_(None),
                )
                .order_by(Application.id)
                .limit(batch_size)
                .all()
            )
            if not rows:
                break
            for application in rows:
                compute_skill_gap(db, application)
            last_id = rows[-1].id
            completed += len(rows)
            db.commit()
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    print(f"Backfilled {backfill(args.batch_size)} skill-gap snapshots")


if __name__ == "__main__":
    main()
