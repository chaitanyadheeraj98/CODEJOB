"""Reprocess sent RecruiterEmail rows for a given day through
PhoneIntelligenceWorkflowService, so already-sent items pick up the
recruiter/employer classification, company-name derivation, and
employer_email fixes without needing a fresh inbound email.

Safe to re-run: PremiumNumberLead/PremiumNumberContact/NumberReviewQueue/
RecruiterOpportunity writes in the workflow service are all upsert-by-key,
so this updates existing rows in place rather than duplicating them.

A second pass then patches contacts that were already linked before today
(their `active_recruiter_lead_id`/`active_employer_lead_id` pointer was
already set on a prior capture). `_link_lead_to_contact` only calls
apply_contact_version() the first time that pointer is set - deliberately,
so a contact's displayed identity stays stable across noisy re-extractions
instead of flip-flopping on every repeat email, with the "select a version"
UI as the escape hatch. That means fields that plain didn't exist yet at
first-link time (employer_email) or that only got their fallback logic
today (company still "Unknown") never reach an already-linked contact
through reprocessing alone. This pass fills only those still-blank fields
from the contact's own active lead - it never overwrites a populated value.

Usage: python scripts/backfill_todays_phone_intelligence.py --date 2026-08-24
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import PremiumNumberContact, PremiumNumberLead, RecruiterEmail  # noqa: E402
from app.services.phone_intelligence_workflow_service import (  # noqa: E402
    PhoneIntelligenceWorkflowService,
    company_fallback_for_unknown,
)


def backfill(target_date: date) -> dict[str, int]:
    stats = {"processed": 0, "recruiter_matches": 0, "employer_matches": 0, "review_created": 0, "errors": 0}
    service = PhoneIntelligenceWorkflowService()
    with SessionLocal() as db:
        rows = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == settings.owner_id,
                RecruiterEmail.sent_status == "sent",
                func.date(RecruiterEmail.sent_at) == target_date,
            )
            .order_by(RecruiterEmail.id)
            .all()
        )
        for email in rows:
            try:
                result = service.capture_premium_numbers(db, email)
                db.commit()
                stats["processed"] += 1
                stats["recruiter_matches"] += result.recruiter_matches
                stats["employer_matches"] += result.employer_matches
                stats["review_created"] += result.review_created
            except Exception as exc:
                db.rollback()
                stats["errors"] += 1
                print(f"  email_id={email.id}: {exc}")
    return stats


def backfill_missing_contact_fields(target_date: date) -> dict[str, int]:
    stats = {"contacts_checked": 0, "employer_email_filled": 0, "company_filled": 0}
    with SessionLocal() as db:
        email_ids = [
            row.id
            for row in db.query(RecruiterEmail.id).filter(
                RecruiterEmail.owner_id == settings.owner_id,
                RecruiterEmail.sent_status == "sent",
                func.date(RecruiterEmail.sent_at) == target_date,
            )
        ]
        contact_ids = {
            row.contact_id
            for row in db.query(PremiumNumberLead.contact_id).filter(
                PremiumNumberLead.owner_id == settings.owner_id,
                PremiumNumberLead.recruiter_email_id.in_(email_ids),
                PremiumNumberLead.contact_id.isnot(None),
            )
        }
        for contact_id in contact_ids:
            contact = db.query(PremiumNumberContact).filter(PremiumNumberContact.id == contact_id).first()
            if contact is None:
                continue
            stats["contacts_checked"] += 1

            if contact.is_employer and contact.active_employer_lead_id and not contact.employer_email.strip():
                lead = (
                    db.query(PremiumNumberLead)
                    .filter(PremiumNumberLead.id == contact.active_employer_lead_id)
                    .first()
                )
                if lead and lead.contact_email:
                    contact.employer_email = lead.contact_email
                    stats["employer_email_filled"] += 1

            if not contact.company or contact.company.strip().lower() == "unknown":
                active_lead_id = contact.active_recruiter_lead_id or contact.active_employer_lead_id
                lead = (
                    db.query(PremiumNumberLead).filter(PremiumNumberLead.id == active_lead_id).first()
                    if active_lead_id
                    else None
                )
                fallback = company_fallback_for_unknown(db, contact.owner_id, lead.contact_email if lead else None)
                if fallback:
                    contact.company = fallback
                    stats["company_filled"] += 1

            db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="YYYY-MM-DD, matched against RecruiterEmail.sent_at")
    args = parser.parse_args()
    target_date = date.fromisoformat(args.date)
    stats = backfill(target_date)
    print(
        f"Reprocessed {stats['processed']} sent emails for {target_date} "
        f"({stats['recruiter_matches']} recruiter matches, {stats['employer_matches']} employer matches, "
        f"{stats['review_created']} review rows created, {stats['errors']} errors)"
    )
    field_stats = backfill_missing_contact_fields(target_date)
    print(
        f"Patched {field_stats['contacts_checked']} already-linked contacts touched today "
        f"({field_stats['employer_email_filled']} employer_email filled, {field_stats['company_filled']} company filled)"
    )


if __name__ == "__main__":
    main()
