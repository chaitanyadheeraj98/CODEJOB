"""Repair premium contacts from stored leads; optionally re-extract a reviewed source scope."""

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
from app.premium_numbers.extraction import ExtractedContactGroup  # noqa: E402
from app.premium_numbers.identity_matching import classify_identity_match  # noqa: E402
from app.services.phone_intelligence_workflow_service import (  # noqa: E402
    PhoneIntelligenceWorkflowService,
    apply_contact_version,
)


IDENTITY_FIELDS = (
    "recruiter_name",
    "recruiter_email",
    "owner_name",
    "employer_email",
    "company",
    "designation",
    "linkedin_url",
)


def _candidate(lead: PremiumNumberLead) -> ExtractedContactGroup:
    return ExtractedContactGroup(
        phone_number_display=lead.phone_number_display,
        phone_number_normalized=lead.phone_number_normalized,
        owner_name=lead.owner_name,
        contact_email=lead.contact_email,
        company=lead.company,
        designation=lead.designation,
        purpose=lead.purpose,
        confidence=lead.confidence,
        contact_type=lead.contact_type,
        recruiter_relevance_score=lead.recruiter_relevance_score,
        is_recruiter_relevant=lead.is_recruiter_relevant,
        relevance_reason=lead.relevance_reason,
        source_fragment=lead.source_fragment,
        role=lead.role,
        extraction_source=lead.extraction_source,
        linkedin_url=lead.linkedin_url,
        source_section=lead.source_section or "unknown",
        block_id=lead.block_id or "",
        evidence_offset_start=lead.evidence_offset_start,
        evidence_offset_end=lead.evidence_offset_end,
        colocation_verified=lead.colocation_verified,
    )


def _scoped_contact_ids(db, contact_ids: list[int], start: date | None, end: date | None) -> set[int]:
    if contact_ids:
        return set(contact_ids)
    email_query = db.query(RecruiterEmail.id).filter(
        RecruiterEmail.owner_id == settings.owner_id,
        RecruiterEmail.sent_status == "sent",
    )
    if start:
        email_query = email_query.filter(func.date(RecruiterEmail.sent_at) >= start)
    if end:
        email_query = email_query.filter(func.date(RecruiterEmail.sent_at) <= end)
    email_ids = [row.id for row in email_query]
    return {
        row.contact_id
        for row in db.query(PremiumNumberLead.contact_id).filter(
            PremiumNumberLead.owner_id == settings.owner_id,
            PremiumNumberLead.recruiter_email_id.in_(email_ids),
            PremiumNumberLead.contact_id.is_not(None),
        )
    }


def repair(*, contact_ids: list[int], start: date | None, end: date | None, reextract: bool, dry_run: bool) -> dict[str, int]:
    stats = {"contacts_checked": 0, "contacts_changed": 0, "review_needed": 0, "emails_reextracted": 0}
    with SessionLocal() as db:
        scoped_ids = _scoped_contact_ids(db, contact_ids, start, end)
        contacts = db.query(PremiumNumberContact).filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id.in_(scoped_ids),
        ).all()
        for contact in contacts:
            stats["contacts_checked"] += 1
            before = {field: getattr(contact, field) for field in IDENTITY_FIELDS}
            for role, lead_id in (
                ("recruiter", contact.active_recruiter_lead_id),
                ("employer", contact.active_employer_lead_id),
            ):
                if not lead_id or contact.recruiter_verification_level in {"verified", "trusted"}:
                    continue
                lead = db.get(PremiumNumberLead, lead_id)
                if not lead:
                    continue
                match = classify_identity_match(contact, _candidate(lead), role)
                if match.outcome != "confirmed":
                    stats["review_needed"] += 1
                    continue
                apply_contact_version(db, contact, lead, role)
            after = {field: getattr(contact, field) for field in IDENTITY_FIELDS}
            changes = {field: [before[field], after[field]] for field in IDENTITY_FIELDS if before[field] != after[field]}
            if changes:
                stats["contacts_changed"] += 1
                print(f"contact_id={contact.id} changes={changes}")

        if reextract:
            email_ids = {
                row.recruiter_email_id
                for row in db.query(PremiumNumberLead.recruiter_email_id).filter(
                    PremiumNumberLead.owner_id == settings.owner_id,
                    PremiumNumberLead.contact_id.in_(scoped_ids),
                    PremiumNumberLead.recruiter_email_id.is_not(None),
                )
            }
            service = PhoneIntelligenceWorkflowService(manage_transaction=False)
            for email in db.query(RecruiterEmail).filter(RecruiterEmail.id.in_(email_ids)).order_by(RecruiterEmail.id):
                service.capture_premium_numbers(db, email)
                stats["emails_reextracted"] += 1

        db.rollback() if dry_run else db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--contact-ids", nargs="*", type=int, default=[])
    parser.add_argument("--reextract", action="store_true", help="Opt in to LLM extraction for the scoped contacts")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show field-level changes and roll back instead of committing",
    )
    args = parser.parse_args()
    if not args.contact_ids and not args.start_date and not args.end_date:
        parser.error("provide --contact-ids or a date range")
    if args.start_date and args.end_date and args.start_date > args.end_date:
        parser.error("--start-date must not be after --end-date")
    print(repair(
        contact_ids=args.contact_ids,
        start=args.start_date,
        end=args.end_date,
        reextract=args.reextract,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
