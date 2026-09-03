from __future__ import annotations

import argparse
from collections import Counter
from email.utils import parseaddr

from app.config import settings
from app.db import SessionLocal
from app.models import PremiumContactEmail, PremiumContactPhone, PremiumNumberContact, RecruiterEmail
from app.premium_numbers.contact_identity_service import reconcile


def run(*, batch_size: int, min_id: int, dry_run: bool) -> Counter[str]:
    db = SessionLocal()
    stats: Counter[str] = Counter()
    try:
        before = (
            db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == settings.owner_id).count(),
            db.query(PremiumContactEmail).filter(PremiumContactEmail.owner_id == settings.owner_id).count(),
            db.query(PremiumContactPhone).filter(PremiumContactPhone.owner_id == settings.owner_id).count(),
        )
        last_id = min_id - 1
        while True:
            rows = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id > last_id).order_by(RecruiterEmail.id.asc()).limit(batch_size).all()
            if not rows:
                break
            for row in rows:
                try:
                    name, email = parseaddr(row.sender or "")
                    result = reconcile(db, owner_id=row.owner_id, normalized_email=email, name=name or email, company=row.company or "Unknown", role="recruiter", source_email_id=row.id, human_confirmed=False)
                    row.resolved_recruiter_email = email.strip().lower() or None
                    row.resolved_recruiter_contact_id = result.contact.id if result.contact else None
                    stats[result.status] += 1
                    db.rollback() if dry_run else db.commit()
                except Exception:
                    db.rollback()
                    stats["errors"] += 1
            last_id = rows[-1].id
            print(f"batch ending id={last_id}: {dict(stats)}")
        after = (
            db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == settings.owner_id).count(),
            db.query(PremiumContactEmail).filter(PremiumContactEmail.owner_id == settings.owner_id).count(),
            db.query(PremiumContactPhone).filter(PremiumContactPhone.owner_id == settings.owner_id).count(),
        )
        print(f"before contacts/emails/phones={before}; after={after}; delta={tuple(right-left for left, right in zip(before, after))}")
        return stats
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--min-id", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(dict(run(batch_size=args.batch_size, min_id=args.min_id, dry_run=args.dry_run)))
