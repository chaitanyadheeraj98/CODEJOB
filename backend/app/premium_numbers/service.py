from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PremiumNumberLead, RecruiterEmail, UserSettings
from app.premium_numbers.extraction import extract_phone_leads


def extract_and_store_premium_numbers(db: Session, email: RecruiterEmail) -> int:
    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == email.owner_id).first()
    employer_domains = {
        part.strip().lower()
        for part in (user_settings.employer_domains.split(",") if user_settings and user_settings.employer_domains else [])
        if part.strip()
    }
    leads = extract_phone_leads(
        email.sender or "",
        email.subject or "",
        email.body or "",
        employer_domains=employer_domains,
    )
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
            existing.contact_type = lead.contact_type
            existing.recruiter_relevance_score = lead.recruiter_relevance_score
            existing.is_recruiter_relevant = lead.is_recruiter_relevant
            existing.relevance_reason = lead.relevance_reason
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
                contact_type=lead.contact_type,
                recruiter_relevance_score=lead.recruiter_relevance_score,
                is_recruiter_relevant=lead.is_recruiter_relevant,
                relevance_reason=lead.relevance_reason,
                source_fragment=lead.source_fragment,
                source_email_sender=email.sender,
                source_email_subject=email.subject,
                source_email_message_id=email.external_message_id,
            )
        )
        stored += 1
    return stored
