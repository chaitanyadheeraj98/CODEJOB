from __future__ import annotations

from email.utils import parseaddr

from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import RecruiterEmail
from app.phase0 import email_domain
from app.premium_numbers.domain_guard import employer_domains_for_owner


def _clean(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text and text.casefold() != "unknown" else None


def _email(value: object) -> str | None:
    text = _clean(value)
    address = _clean(parseaddr(text or "")[1])
    return address if address and "@" in address and email_domain(address) else None


def _external_opportunity(db: Session, email: RecruiterEmail) -> ExternalOpportunity | None:
    message_id = (email.external_message_id or "").strip()
    if not message_id.casefold().startswith("nvoids:"):
        return None
    return db.query(ExternalOpportunity).filter(
        ExternalOpportunity.owner_id == email.owner_id,
        ExternalOpportunity.external_post_id == message_id[len("nvoids:"):].strip(),
    ).first()


def stamp_recruiter_email_identity(db: Session, email: RecruiterEmail) -> tuple[str | None, str | None]:
    """Persist the stable recruiter email join key without creating a contact."""
    sender_name, raw_sender_address = parseaddr(email.sender or "")
    sender_name = _clean(sender_name)
    sender_address = _email(raw_sender_address)
    external = _external_opportunity(db, email) if email.source == "nvoids" else None
    employer_domains = employer_domains_for_owner(db, email.owner_id)

    def non_employer(address: str | None) -> str | None:
        cleaned = _email(address)
        return cleaned if cleaned and email_domain(cleaned) not in employer_domains else None

    recruiter_email = (
        _email(external.recruiter_email if external else None)
        or non_employer(email.recipient_email)
        or non_employer(sender_address)
    )
    normalized = recruiter_email.casefold() if recruiter_email else None
    email.resolved_recruiter_email = normalized
    return normalized, sender_name if normalized == (sender_address or "").casefold() else None
