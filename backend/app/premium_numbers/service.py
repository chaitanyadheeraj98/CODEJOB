from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PremiumNumberLead, RecruiterEmail
from app.premium_numbers.extraction import extract_phone_leads


def extract_and_store_premium_numbers(db: Session, email: RecruiterEmail) -> int:
    leads = extract_phone_leads(email.sender or "", email.subject or "", email.body or "")
    stored = 0
    for lead in leads:
        existing = (
            db.query(PremiumNumberLead)
            .filter(
                PremiumNumberLead.owner_id == email.owner_id,
                PremiumNumberLead.recruiter_email_id == email.id,
                PremiumNumberLead.phone_number_normalized == lead.phone_number_normalized,
            )
            .first()
        )
        if existing:
            existing.phone_number_display = lead.phone_number_display
            existing.owner_name = lead.owner_name
            existing.company = lead.company
            existing.designation = lead.designation
            existing.purpose = lead.purpose
            existing.confidence = lead.confidence
            existing.source_fragment = lead.source_fragment
            existing.source_email_sender = email.sender
            existing.source_email_subject = email.subject
            existing.source_email_message_id = email.external_message_id
            stored += 1
            continue
        db.add(
            PremiumNumberLead(
                owner_id=email.owner_id,
                recruiter_email_id=email.id,
                phone_number_normalized=lead.phone_number_normalized,
                phone_number_display=lead.phone_number_display,
                owner_name=lead.owner_name,
                company=lead.company,
                designation=lead.designation,
                purpose=lead.purpose,
                confidence=lead.confidence,
                source_fragment=lead.source_fragment,
                source_email_sender=email.sender,
                source_email_subject=email.subject,
                source_email_message_id=email.external_message_id,
            )
        )
        stored += 1
    return stored
