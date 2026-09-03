"""Fix two data-quality issues on premium_number_contacts found via the Number
Inventory "E-Domain" column showing a stray ">" (e.g. "horizonsoftech.net>"):

1. recruiter_email/employer_email still holding a raw "Name <address>" string
   instead of a clean address (legacy rows predating proper sender parsing).
2. recruiter_email_domain/employer_email_domain going stale after the email
   was later corrected (fixed going forward in apply_contact_version /
   _apply_unversioned_contact_fields; this script repairs existing rows).

Both are fixed the same way: re-derive the clean address via parseaddr() and
recompute the domain via email_domain() - the same helper the app now uses.
Only touches rows where recomputing would actually change something.

Usage: python scripts/backfill_contact_email_domains.py [--dry-run]
"""

from __future__ import annotations

import argparse
from email.utils import parseaddr
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import PremiumNumberContact  # noqa: E402
from app.phase0 import email_domain  # noqa: E402


def _clean_address(raw: str) -> str:
    _name, address = parseaddr(raw or "")
    return address.strip().lower()


def backfill(*, dry_run: bool) -> dict[str, int]:
    stats = {"contacts_checked": 0, "recruiter_fixed": 0, "employer_fixed": 0}
    with SessionLocal() as db:
        contacts = db.query(PremiumNumberContact).filter(PremiumNumberContact.deleted_at.is_(None)).all()
        for contact in contacts:
            stats["contacts_checked"] += 1
            changed = False

            if contact.recruiter_email:
                clean = _clean_address(contact.recruiter_email)
                domain = email_domain(clean) if clean else ""
                if clean != contact.recruiter_email or domain != contact.recruiter_email_domain:
                    print(f"  contact={contact.id} recruiter_email: {contact.recruiter_email!r} -> {clean!r}, domain: {contact.recruiter_email_domain!r} -> {domain!r}")
                    if not dry_run:
                        contact.recruiter_email = clean
                        contact.recruiter_email_domain = domain
                    stats["recruiter_fixed"] += 1
                    changed = True

            if contact.employer_email:
                clean = _clean_address(contact.employer_email)
                domain = email_domain(clean) if clean else ""
                if clean != contact.employer_email or domain != contact.employer_email_domain:
                    print(f"  contact={contact.id} employer_email: {contact.employer_email!r} -> {clean!r}, domain: {contact.employer_email_domain!r} -> {domain!r}")
                    if not dry_run:
                        contact.employer_email = clean
                        contact.employer_email_domain = domain
                    stats["employer_fixed"] += 1
                    changed = True

            if changed and not dry_run:
                db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Log what would change without writing")
    args = parser.parse_args()
    stats = backfill(dry_run=args.dry_run)
    mode = "DRY RUN - " if args.dry_run else ""
    print(
        f"{mode}Checked {stats['contacts_checked']} contacts: "
        f"{stats['recruiter_fixed']} recruiter_email fixed, {stats['employer_fixed']} employer_email fixed"
    )


if __name__ == "__main__":
    main()
