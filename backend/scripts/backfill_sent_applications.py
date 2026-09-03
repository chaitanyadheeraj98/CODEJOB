"""Backfill Application rows for RecruiterEmail sends made before the
Run Queue / Needs Review auto-tracking hook existed.

Safe to re-run: create_application_from_recruiter_email() is keyed on a
per-email dedupe_key, so an already-logged send is skipped, not duplicated.

Usage: python scripts/backfill_sent_applications.py --batch-size 100
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import RecruiterEmail  # noqa: E402
from app.services.resume_tracking_service import create_application_from_recruiter_email  # noqa: E402


def backfill(batch_size: int) -> dict[str, int]:
    stats = {"logged": 0, "already_tracked": 0, "skipped_no_resume": 0, "skipped_error": 0}
    last_id = 0
    with SessionLocal() as db:
        while True:
            rows = (
                db.query(RecruiterEmail)
                .filter(
                    RecruiterEmail.id > last_id,
                    RecruiterEmail.owner_id == settings.owner_id,
                    RecruiterEmail.sent_status == "sent",
                )
                .order_by(RecruiterEmail.id)
                .limit(batch_size)
                .all()
            )
            if not rows:
                break
            for email in rows:
                try:
                    result = create_application_from_recruiter_email(db, email, owner_id=settings.owner_id)
                    if result is None:
                        stats["skipped_no_resume"] += 1
                    else:
                        _application, created = result
                        stats["logged" if created else "already_tracked"] += 1
                    db.commit()
                except Exception as exc:
                    # Roll back only this row's uncommitted work -- everything
                    # before it in the batch was already committed above.
                    db.rollback()
                    stats["skipped_error"] += 1
                    print(f"  email_id={email.id}: {exc}")
            last_id = rows[-1].id
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    stats = backfill(args.batch_size)
    print(
        f"Logged {stats['logged']} sent emails as Resume Tracking applications "
        f"({stats['already_tracked']} already tracked, {stats['skipped_no_resume']} skipped: no resume attached, "
        f"{stats['skipped_error']} skipped: error)"
    )


if __name__ == "__main__":
    main()
