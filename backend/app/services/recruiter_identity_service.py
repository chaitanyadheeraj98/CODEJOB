from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from email.utils import parseaddr

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import PremiumNumberContact, RecruiterEmail
from app.phase0 import email_domain
from app.premium_numbers import contact_identity_service
from app.premium_numbers.domain_guard import (
    employer_domains_for_owner,
    is_derivable_company_domain,
)


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


@dataclass(frozen=True)
class RecruiterIdentity:
    """Who a send should be recorded against, and what the mail says about them."""

    name: str
    company: str
    address: str
    contact_id: int | None


def sender_company_for(email: RecruiterEmail, address: str | None) -> str:
    """`email.company`, but only when it is this address's company to claim.

    The extractor defines that field as the *sender's* firm - "the vendor/staffing
    company that sent this email ... identify it only from the From-line display
    name, email signature, footer, or sender's email domain"
    (parsing/ai_extractor.py). A forwarded requirement therefore carries the
    forwarder's company, never the recruiter's. Attributing it across a domain
    boundary files a person under a firm the mail never named for them, which is
    the same mistake as inventing an address - one parsing/manual_contacts.py
    already refuses to make.

    Returns "" when it cannot be attributed. Blank means *not identified*.
    """
    company = _clean(email.company)
    if not company:
        return ""
    target = _email(address)
    sender = _email(parseaddr(email.sender or "")[1])
    if not target or not sender:
        return ""
    return company if email_domain(target) == email_domain(sender) else ""


_DOMAIN_COMPANY_CACHE = "recruiter_identity_service.domain_company"


def _domain_company(db: Session, owner_id: str, domain: str) -> str:
    """The firm the people writing from `domain` have named as their own."""
    counts: Counter[str] = Counter()
    for (company,) in (
        db.query(PremiumNumberContact.company)
        .filter(
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.deleted_at.is_(None),
            sa.func.lower(sa.func.trim(PremiumNumberContact.recruiter_email_domain)) == domain,
        )
        .all()
    ):
        if name := _clean(company):
            counts[name] += 1
    if not counts:
        # No contact on the domain says anything, so fall back to the mails
        # themselves. `company` is the sender's own firm by the extractor's
        # definition, so a mail *from* this domain is that firm naming itself.
        for sender, company in (
            db.query(RecruiterEmail.sender, RecruiterEmail.company)
            .filter(
                RecruiterEmail.owner_id == owner_id,
                RecruiterEmail.sender.ilike(f"%@{domain}%"),
            )
            .all()
        ):
            name = _clean(company)
            if name and email_domain(_email(parseaddr(sender or "")[1]) or "") == domain:
                counts[name] += 1
    if not counts:
        return ""
    # Most stated wins; ties go alphabetically so the same domain always renders
    # the same way rather than shifting with row order.
    return min(counts.items(), key=lambda item: (-item[1], item[0]))[0]


def domain_company_for(db: Session, owner_id: str, address: str | None) -> str:
    """The company the people on this address's domain state for themselves.

    A recruiter's own mail rarely names their firm: `email.company` is read off
    the From-line and signature of whoever *sent* the requirement, so a recruiter
    who was merely forwarded one is left with nothing. That is why 2,969 of the
    3,400 tracked sends show no company at all - not because the firm is unknown,
    but because nobody asked the right rows. The domain has been named elsewhere,
    by contacts filed under it and by senders writing from it, and that is a
    statement *about this address* rather than a guess from its spelling.

    `is_derivable_company_domain` keeps free-mail and the owner's own domains out:
    gmail.com names a person, not an employer.

    Cached on the session, because a page of cards asks the same few domains over
    and over.
    """
    cleaned = _email(address)
    domain = email_domain(cleaned or "")
    if not domain or not is_derivable_company_domain(db, owner_id, cleaned):
        return ""
    cache = db.info.setdefault(_DOMAIN_COMPANY_CACHE, {})
    key = (owner_id, domain)
    if key not in cache:
        cache[key] = _domain_company(db, owner_id, domain)
    return cache[key]


def recruiter_identity_for(db: Session, email: RecruiterEmail, *, owner_id: str) -> RecruiterIdentity:
    """The recruiter a send should be recorded against.

    `stamp_recruiter_email_identity` above already worked this out and stored it on
    the row, but neither application service read it: both snapshotted
    `parseaddr(email.sender)`, which on a forwarded requirement is whoever passed
    it along rather than the person the resume went to. Measured on the tracked
    rows, those were different people 1,770 times out of 3,386.

    Prefers what is recorded over what is guessed - a contact record, then the
    From-line name but only when the sender *is* the recruiter, then the address
    itself. Never a name belonging to somebody else.
    """
    sender_name, raw_sender = parseaddr(email.sender or "")
    sender_address = _email(raw_sender) or ""
    address = (email.resolved_recruiter_email or "").strip().casefold() or sender_address.casefold()
    contact = (
        contact_identity_service.find_email_owner(db, owner_id, address, "recruiter")
        if address
        else None
    )
    sender_is_recruiter = bool(address) and address == sender_address.casefold()
    name = (
        _clean(contact.recruiter_name if contact else None)
        or (_clean(sender_name) if sender_is_recruiter else None)
        or address
    )
    company = (
        _clean(contact.company if contact else None)
        or sender_company_for(email, address)
        or domain_company_for(db, owner_id, address)
    )
    return RecruiterIdentity(
        name=name,
        company=company or "",
        address=address,
        contact_id=contact.id if contact else None,
    )
