from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    AppTSApplication,
    ContactIdentityAction,
    NumberReviewQueue,
    PremiumContactEmail,
    PremiumContactPhone,
    PremiumNumberContact,
    RecruiterEmail,
    utc_now,
)
from app.premium_numbers.extraction import ExtractedContactGroup
from app.premium_numbers.identity_matching import classify_identity_match
from app.premium_numbers.phone_normalization import best_display_phone


@dataclass(frozen=True)
class ReconcileResult:
    contact: PremiumNumberContact | None
    status: str
    review_id: int | None = None


def normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _find_by_phone(db: Session, owner_id: str, value: str) -> PremiumNumberContact | None:
    child = db.query(PremiumContactPhone.premium_contact_id).filter(PremiumContactPhone.owner_id == owner_id, PremiumContactPhone.normalized_phone_number == value)
    return db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.deleted_at.is_(None), or_(PremiumNumberContact.normalized_phone_number == value, PremiumNumberContact.id.in_(child))).first()


def _find_by_email(db: Session, owner_id: str, value: str) -> PremiumNumberContact | None:
    child = db.query(PremiumContactEmail.premium_contact_id).filter(PremiumContactEmail.owner_id == owner_id, PremiumContactEmail.normalized_email == value)
    return db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.deleted_at.is_(None), or_(PremiumNumberContact.recruiter_email == value, PremiumNumberContact.employer_email == value, PremiumNumberContact.id.in_(child))).first()


def _candidate(email: str, name: str, company: str, role: str) -> ExtractedContactGroup:
    return ExtractedContactGroup(
        phone_number_display="", phone_number_normalized="", owner_name=name or email or "Unknown",
        contact_email=email, company=company or "Unknown", designation="Unknown", purpose="", confidence="medium",
        contact_type=role, recruiter_relevance_score=0, is_recruiter_relevant=True, relevance_reason="",
        source_fragment="", role=role, extraction_source="identity_reconcile",
    )


def _action(db: Session, *, owner_id: str, action_type: str, primary_contact_id: int, source: str, secondary_contact_id: int | None = None, value: str | None = None) -> None:
    db.add(ContactIdentityAction(owner_id=owner_id, action_type=action_type, primary_contact_id=primary_contact_id, secondary_contact_id=secondary_contact_id, value=value, source=source, created_at=utc_now()))


def _add_email(db: Session, contact: PremiumNumberContact, email: str, source_email_id: int | None, *, primary: bool) -> None:
    try:
        with db.begin_nested():
            db.add(PremiumContactEmail(owner_id=contact.owner_id, premium_contact_id=contact.id, normalized_email=email, domain=email.rpartition("@")[2], is_primary=primary, source_email_id=source_email_id, created_at=utc_now()))
            db.flush()
    except IntegrityError:
        return
    if primary or not contact.recruiter_email:
        contact.recruiter_email = email
        contact.recruiter_email_domain = email.rpartition("@")[2]


def _add_phone(db: Session, contact: PremiumNumberContact, phone: str, *, primary: bool) -> None:
    try:
        with db.begin_nested():
            db.add(PremiumContactPhone(owner_id=contact.owner_id, premium_contact_id=contact.id, normalized_phone_number=phone, is_primary=primary, is_verified=False, source="identity_reconcile", created_at=utc_now()))
            db.flush()
    except IntegrityError:
        return
    if primary or not contact.normalized_phone_number:
        contact.normalized_phone_number = phone
        contact.display_phone_number = best_display_phone(phone, fallback=phone)


def _create_contact(db: Session, *, owner_id: str, phone: str, email: str, name: str, company: str, role: str, source_email_id: int | None) -> PremiumNumberContact:
    contact = PremiumNumberContact(
        owner_id=owner_id, normalized_phone_number=phone or None, display_phone_number=best_display_phone(phone, fallback=phone) if phone else "",
        is_recruiter=role == "recruiter", is_employer=role == "employer",
        recruiter_name=name or "Unknown", owner_name=name or "Unknown", recruiter_email=email,
        recruiter_email_domain=email.rpartition("@")[2] if email else "", company=company or "Unknown",
        first_detected_email_id=source_email_id, source_email_id=source_email_id, created_at=utc_now(), updated_at=utc_now(),
    )
    with db.begin_nested():
        db.add(contact)
        db.flush()
    if email:
        _add_email(db, contact, email, source_email_id, primary=True)
    if phone:
        _add_phone(db, contact, phone, primary=True)
    _action(db, owner_id=owner_id, action_type="create", primary_contact_id=contact.id, source="automatic")
    return contact


def _review(db: Session, *, owner_id: str, target: PremiumNumberContact, reason: str, phone: str, email: str, source_email_id: int | None, secondary: PremiumNumberContact | None = None) -> NumberReviewQueue:
    value_filter = NumberReviewQueue.contact_email == email if email else NumberReviewQueue.normalized_phone_number == phone
    existing = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == owner_id, NumberReviewQueue.target_contact_id == target.id, NumberReviewQueue.reason_code == reason, value_filter, NumberReviewQueue.state.in_(("pending", "dismissed"))).first()
    if existing:
        return existing
    row = NumberReviewQueue(
        owner_id=owner_id, source_email_id=source_email_id, target_contact_id=target.id,
        secondary_contact_id=secondary.id if secondary else None, normalized_phone_number=phone or target.normalized_phone_number or "",
        display_phone_number=phone or target.display_phone_number or "", owner_name=target.recruiter_name,
        company=target.company, contact_email=email, contact_type="recruiter", role="recruiter",
        reason_code=reason, state="pending", created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def reconcile(db: Session, *, owner_id: str, normalized_phone: str = "", normalized_email: str = "", name: str = "", company: str = "", role: str = "recruiter", source_email_id: int | None = None, human_confirmed: bool = False) -> ReconcileResult:
    phone, email = normalized_phone.strip(), normalize_email(normalized_email)
    by_phone = _find_by_phone(db, owner_id, phone) if phone else None
    by_email = _find_by_email(db, owner_id, email) if email else None
    if by_phone is None and by_email is None:
        return ReconcileResult(_create_contact(db, owner_id=owner_id, phone=phone, email=email, name=name, company=company, role=role, source_email_id=source_email_id), "created")
    if by_phone is not None and by_email is not None and by_phone.id != by_email.id:
        review = _review(db, owner_id=owner_id, target=by_phone, secondary=by_email, reason="phone_email_cross_conflict", phone=phone, email=email, source_email_id=source_email_id)
        return ReconcileResult(by_phone, "pending_merge_approval", review.id)
    if by_phone is not None and by_email is None:
        match = classify_identity_match(by_phone, _candidate(email, name, company, role), role)
        if match.outcome == "confirmed" and email and human_confirmed:
            _add_email(db, by_phone, email, source_email_id, primary=False)
            _action(db, owner_id=owner_id, action_type="link_email", primary_contact_id=by_phone.id, value=email, source="human_entry_confirmed")
            return ReconcileResult(by_phone, "confirmed")
        reason = "suggested_email_match" if match.outcome == "confirmed" else "identity_conflict" if match.outcome == "conflicting" else "insufficient_evidence"
        review = _review(db, owner_id=owner_id, target=by_phone, reason=reason, phone=phone, email=email, source_email_id=source_email_id)
        return ReconcileResult(by_phone if human_confirmed else None, "pending_link_approval", review.id)
    if by_email is not None and by_phone is None:
        match = classify_identity_match(by_email, _candidate(email, name, company, role), role)
        if match.outcome == "confirmed" and phone and human_confirmed:
            _add_phone(db, by_email, phone, primary=not bool(by_email.normalized_phone_number))
            _action(db, owner_id=owner_id, action_type="link_phone", primary_contact_id=by_email.id, value=phone, source="human_entry_confirmed")
            return ReconcileResult(by_email, "confirmed")
        if phone:
            reason = "suggested_phone_match" if match.outcome == "confirmed" else "identity_conflict" if match.outcome == "conflicting" else "insufficient_evidence"
            review = _review(db, owner_id=owner_id, target=by_email, reason=reason, phone=phone, email="", source_email_id=source_email_id)
            return ReconcileResult(by_email if human_confirmed else None, "pending_link_approval", review.id)
        return ReconcileResult(by_email, "confirmed")
    assert by_phone is not None
    for field, value in (("recruiter_name", name), ("company", company)):
        if value and getattr(by_phone, field) in ("", "Unknown"):
            setattr(by_phone, field, value)
    return ReconcileResult(by_phone, "confirmed")


def approve_link(db: Session, review: NumberReviewQueue) -> PremiumNumberContact:
    contact = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == review.target_contact_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if contact is None:
        raise ValueError("Target contact not found")
    if review.contact_email:
        value, action = normalize_email(review.contact_email), "link_email"
        _add_email(db, contact, value, review.source_email_id, primary=not bool(contact.recruiter_email))
        for model in (RecruiterEmail, AppTSApplication):
            db.query(model).filter(model.owner_id == review.owner_id, model.resolved_recruiter_email == value).update({model.resolved_recruiter_contact_id: contact.id}, synchronize_session=False)
    else:
        value, action = review.normalized_phone_number, "link_phone"
        _add_phone(db, contact, value, primary=not bool(contact.normalized_phone_number))
    _action(db, owner_id=review.owner_id, action_type=action, primary_contact_id=contact.id, value=value, source="suggestion_approved")
    review.state = "resolved"
    return contact


def approve_merge(db: Session, review: NumberReviewQueue, canonical_contact_id: int | None = None) -> PremiumNumberContact:
    ids = {review.target_contact_id, review.secondary_contact_id}
    canonical_id = canonical_contact_id or review.target_contact_id
    if None in ids or canonical_id not in ids:
        raise ValueError("Invalid merge contacts")
    loser_id = next(contact_id for contact_id in ids if contact_id != canonical_id)
    canonical = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == canonical_id, PremiumNumberContact.deleted_at.is_(None)).first()
    loser = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == loser_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if canonical is None or loser is None:
        raise ValueError("Merge contact not found")
    for model, value_column in ((PremiumContactEmail, PremiumContactEmail.normalized_email), (PremiumContactPhone, PremiumContactPhone.normalized_phone_number)):
        for child in db.query(model).filter(model.premium_contact_id == loser.id).all():
            duplicate = db.query(model.id).filter(model.premium_contact_id == canonical.id, value_column == getattr(child, value_column.key)).first()
            if duplicate:
                db.delete(child)
            else:
                child.premium_contact_id = canonical.id
    for model in (RecruiterEmail, AppTSApplication):
        db.query(model).filter(model.owner_id == review.owner_id, model.resolved_recruiter_contact_id == loser.id).update({model.resolved_recruiter_contact_id: canonical.id}, synchronize_session=False)
    loser.deleted_at = utc_now()
    review.state = "resolved"
    _action(db, owner_id=review.owner_id, action_type="merge", primary_contact_id=canonical.id, secondary_contact_id=loser.id, source="suggestion_approved")
    return canonical


def dismiss(review: NumberReviewQueue) -> None:
    review.state = "dismissed"
