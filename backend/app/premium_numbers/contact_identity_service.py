from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import (
    AppTSApplication,
    ContactIdentityAction,
    NumberReviewQueue,
    PremiumContactEmail,
    PremiumContactPhone,
    PremiumNumberContact,
    PremiumNumberLead,
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


ROLE_ADDRESS_LOCAL_PARTS = frozenset({
    "hr", "careers", "career", "jobs", "job", "info", "contact", "recruiting",
    "recruitment", "talent", "hiring", "admin", "support", "sales", "noreply", "no-reply",
})


def is_role_address(email: str) -> bool:
    return email.rpartition("@")[0].split("+")[0].strip().lower() in ROLE_ADDRESS_LOCAL_PARTS


def headline_email_columns(role: str) -> tuple[str, str]:
    """The (value, domain) attribute names this role's headline email lives in."""
    return ("recruiter_email", "recruiter_email_domain") if role == "recruiter" else ("employer_email", "employer_email_domain")


def set_headline_email(contact: PremiumNumberContact, email: str, role: str) -> None:
    """Write the role-correct headline column. The headline is a denormalised cache of
    premium_contact_emails, kept in sync on every write so the two stores cannot drift -
    the drift itself is what let one address look like it belonged to two different
    contacts depending on which code path asked."""
    value_column, domain_column = headline_email_columns(role)
    setattr(contact, value_column, email)
    setattr(contact, domain_column, email.rpartition("@")[2] if email else "")


def _release_headline_email(db: Session, owner_id: str, email: str, keep_contact_id: int) -> None:
    """Clear a stale headline naming `email` on every OTHER live contact. Only
    premium_contact_emails has a unique constraint, so without this a contact can keep
    advertising an address that another contact now genuinely owns."""
    for role in ("recruiter", "employer"):
        value_column, domain_column = headline_email_columns(role)
        db.query(PremiumNumberContact).filter(
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.id != keep_contact_id,
            PremiumNumberContact.deleted_at.is_(None),
            getattr(PremiumNumberContact, value_column) == email,
        ).update({value_column: "", domain_column: ""}, synchronize_session=False)


def phone_claim_owner(db: Session, owner_id: str, phone: str, extension: str = "", exclude_contact_id: int | None = None) -> int | None:
    """Id of the contact already holding `phone`+`extension`, as its own primary OR as a
    secondary row - the two stores that can each independently claim a number. Returns
    None when the slot is free. This is the single guard both add_phone and
    main._apply_phone_list use, so they can no longer disagree about what "taken" means."""
    if not phone:
        return None
    primary = db.query(PremiumNumberContact.id).filter(
        PremiumNumberContact.owner_id == owner_id,
        PremiumNumberContact.normalized_phone_number == phone,
        PremiumNumberContact.phone_extension == extension,
        PremiumNumberContact.deleted_at.is_(None),
    )
    secondary = db.query(PremiumContactPhone.premium_contact_id).filter(
        PremiumContactPhone.owner_id == owner_id,
        PremiumContactPhone.normalized_phone_number == phone,
        PremiumContactPhone.phone_extension == extension,
    )
    if exclude_contact_id is not None:
        primary = primary.filter(PremiumNumberContact.id != exclude_contact_id)
        secondary = secondary.filter(PremiumContactPhone.premium_contact_id != exclude_contact_id)
    hit = primary.scalar()
    return hit if hit is not None else secondary.scalar()


def find_phone_owner(db: Session, owner_id: str, phone: str, extension: str = "") -> PremiumNumberContact | None:
    """Who is reachable at this number? Matches a contact's own primary or any secondary
    row recorded for it.

    A blank extension on either side is "unknown", not proof of difference, so it still
    matches; two explicit and *different* extensions mean different people sharing one
    switchboard (confirmed live: SysMind 609-897-9670 ext 2162 vs ext 2197). A person's
    own other number, held as a genuinely different secondary, always matches regardless.
    """
    if not phone:
        return None
    # The multi-identifier migration copied every contact's own primary into the child
    # table with a blank extension. Without excluding that self-copy, a contact whose
    # primary extension differs from `extension` would still match through it and bypass
    # the extension check below.
    secondary = db.query(PremiumContactPhone.premium_contact_id).join(
        PremiumNumberContact, PremiumNumberContact.id == PremiumContactPhone.premium_contact_id
    ).filter(
        PremiumContactPhone.owner_id == owner_id,
        PremiumContactPhone.normalized_phone_number == phone,
        PremiumNumberContact.normalized_phone_number != phone,
    )
    if extension:
        secondary = secondary.filter(or_(
            PremiumContactPhone.phone_extension == "", PremiumContactPhone.phone_extension == extension,
        ))
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner_id,
        PremiumNumberContact.deleted_at.is_(None),
        or_(PremiumNumberContact.normalized_phone_number == phone, PremiumNumberContact.id.in_(secondary)),
    )
    if extension:
        query = query.filter(or_(
            PremiumNumberContact.phone_extension == "",
            PremiumNumberContact.phone_extension == extension,
            PremiumNumberContact.id.in_(secondary),
        ))
    # Ordered so that several people sharing a switchboard resolve to the same contact
    # every time instead of whichever row the database happened to return first.
    return query.order_by(PremiumNumberContact.id).first()


def find_email_owner(db: Session, owner_id: str, email: str, role: str | None = None) -> PremiumNumberContact | None:
    """Who owns this address? premium_contact_emails is consulted first and is
    authoritative - it is the only store with a uniqueness guarantee. Ownership there is
    role-agnostic (one address, one owner), so `role` narrows only the headline fallback,
    which exists for rows the 0048 backfill could not adopt."""
    if not email:
        return None
    claimed_by = db.query(PremiumContactEmail.premium_contact_id).filter(
        PremiumContactEmail.owner_id == owner_id, PremiumContactEmail.normalized_email == email,
    ).scalar()
    if claimed_by is not None:
        contact = db.query(PremiumNumberContact).filter(
            PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id == claimed_by,
            PremiumNumberContact.deleted_at.is_(None),
        ).first()
        if contact is not None:
            return contact
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.deleted_at.is_(None),
    )
    if role is None:
        query = query.filter(or_(
            PremiumNumberContact.recruiter_email == email, PremiumNumberContact.employer_email == email,
        ))
    else:
        role_flag = PremiumNumberContact.is_recruiter if role == "recruiter" else PremiumNumberContact.is_employer
        query = query.filter(role_flag.is_(True), getattr(PremiumNumberContact, headline_email_columns(role)[0]) == email)
    return query.order_by(PremiumNumberContact.id).first()


@dataclass(frozen=True)
class IdentityResolution:
    """Both answers to "who is this?", kept separate. When the phone and the email point
    at different contacts that is a real three-way collision the reviewer has to see -
    silently preferring one of them is what produced the duplicate contacts this service
    exists to prevent."""
    phone_owner: PremiumNumberContact | None
    email_owner: PremiumNumberContact | None

    @property
    def outcome(self) -> str:
        if self.phone_owner is None and self.email_owner is None:
            return "none"
        if self.phone_owner is not None and self.email_owner is not None and self.phone_owner.id != self.email_owner.id:
            return "split"
        return "single"

    @property
    def contact(self) -> PremiumNumberContact | None:
        """The one contact this identity resolves to, or None when it is unresolved or
        genuinely split between two."""
        return None if self.outcome == "split" else (self.phone_owner or self.email_owner)


def resolve_identity(
    db: Session, *, owner_id: str, phone: str = "", extension: str = "", email: str = "", role: str | None = None
) -> IdentityResolution:
    """The single answer to "which contact does this phone/email belong to?", shared by
    the ingestion pipeline, reconcile() and dismiss(). They previously each had their own
    version with subtly different rules."""
    return IdentityResolution(
        phone_owner=find_phone_owner(db, owner_id, phone.strip(), extension),
        email_owner=None if is_role_address(email) else find_email_owner(db, owner_id, normalize_email(email), role),
    )




def _candidate(email: str, name: str, company: str, role: str) -> ExtractedContactGroup:
    return ExtractedContactGroup(
        phone_number_display="", phone_number_normalized="", owner_name=name or email or "Unknown",
        contact_email=email, company=company or "Unknown", designation="Unknown", purpose="", confidence="medium",
        contact_type=role, recruiter_relevance_score=0, is_recruiter_relevant=True, relevance_reason="",
        source_fragment="", role=role, extraction_source="identity_reconcile",
    )


def _action(db: Session, *, owner_id: str, action_type: str, primary_contact_id: int, source: str, secondary_contact_id: int | None = None, value: str | None = None) -> None:
    db.add(ContactIdentityAction(owner_id=owner_id, action_type=action_type, primary_contact_id=primary_contact_id, secondary_contact_id=secondary_contact_id, value=value, source=source, created_at=utc_now()))


def attach_email(db: Session, contact: PremiumNumberContact, email: str, role: str, source_email_id: int | None, *, primary: bool) -> bool:
    """Link `email` to `contact` in premium_contact_emails and mirror it onto the
    role-correct headline column. Returns False when the address is already on file for a
    *different* contact - the caller must not treat the link as having happened."""
    try:
        with db.begin_nested():
            db.add(PremiumContactEmail(
                owner_id=contact.owner_id, premium_contact_id=contact.id, normalized_email=email,
                domain=email.rpartition("@")[2], is_primary=primary, role=role,
                source_email_id=source_email_id, created_at=utc_now(),
            ))
            db.flush()
    except IntegrityError:
        owner_id = db.query(PremiumContactEmail.premium_contact_id).filter(PremiumContactEmail.owner_id == contact.owner_id, PremiumContactEmail.normalized_email == email).scalar()
        if owner_id != contact.id:
            return False
    if primary or not getattr(contact, headline_email_columns(role)[0]):
        set_headline_email(contact, email, role)
    # This contact now provably owns the address, so nobody else may keep advertising it.
    _release_headline_email(db, contact.owner_id, email, keep_contact_id=contact.id)
    return True


def add_phone(db: Session, contact: PremiumNumberContact, phone: str, *, primary: bool, extension: str = "") -> bool:
    """Returns False when `phone`+`extension` is already on file for a *different* contact -
    the caller must not treat the link as having happened."""
    already_primary = phone == contact.normalized_phone_number and extension == (contact.phone_extension or "")
    becomes_primary = primary or not contact.normalized_phone_number
    if not already_primary:
        # Checked for BOTH promotions and plain secondary adds. The child-table unique
        # constraint only sees other child rows, so on its own it cannot stop this contact
        # recording as a secondary a number that is somebody else's primary - which is
        # exactly how a number ends up owned twice. Setting the contact's OWN
        # normalized_phone_number is also a plain UPDATE, not an insert the nested
        # transaction below would catch, so its violation would otherwise surface later,
        # uncaught, at the caller's own commit.
        if phone_claim_owner(db, contact.owner_id, phone, extension, exclude_contact_id=contact.id) is not None:
            return False
    exists = db.query(PremiumContactPhone.id).filter(
        PremiumContactPhone.owner_id == contact.owner_id, PremiumContactPhone.premium_contact_id == contact.id,
        PremiumContactPhone.normalized_phone_number == phone, PremiumContactPhone.phone_extension == extension,
    ).first()
    if not exists:
        try:
            with db.begin_nested():
                db.add(PremiumContactPhone(owner_id=contact.owner_id, premium_contact_id=contact.id, normalized_phone_number=phone, phone_extension=extension, is_primary=primary, is_verified=False, source="identity_reconcile", created_at=utc_now()))
                db.flush()
        except IntegrityError:
            owner_id = db.query(PremiumContactPhone.premium_contact_id).filter(PremiumContactPhone.owner_id == contact.owner_id, PremiumContactPhone.normalized_phone_number == phone, PremiumContactPhone.phone_extension == extension).scalar()
            if owner_id != contact.id:
                return False
    if becomes_primary and not already_primary:
        if contact.normalized_phone_number:
            # Preserve the outgoing primary as a secondary row instead of discarding it -
            # a number this contact was reachable at a moment ago shouldn't just vanish
            # because a different one took over.
            exists = db.query(PremiumContactPhone.id).filter(
                PremiumContactPhone.owner_id == contact.owner_id, PremiumContactPhone.premium_contact_id == contact.id,
                PremiumContactPhone.normalized_phone_number == contact.normalized_phone_number,
                PremiumContactPhone.phone_extension == (contact.phone_extension or ""),
            ).first()
            if not exists:
                db.add(PremiumContactPhone(
                    owner_id=contact.owner_id, premium_contact_id=contact.id,
                    normalized_phone_number=contact.normalized_phone_number, phone_extension=contact.phone_extension or "",
                    is_primary=False, is_verified=False, source="identity_reconcile", created_at=utc_now(),
                ))
        contact.normalized_phone_number = phone
        contact.phone_extension = extension
        contact.display_phone_number = f"{best_display_phone(phone, fallback=phone)} ext {extension}" if extension else best_display_phone(phone, fallback=phone)
    return True


@dataclass(frozen=True)
class CreatedContact:
    """`unclaimed_*` name an identifier that could NOT be attached because another contact
    already owns it. Silently dropping one is how this service used to mint phoneless
    orphans, so the caller is handed the fact and has to decide what to do about it."""
    contact: PremiumNumberContact
    unclaimed_phone: str = ""
    unclaimed_email: str = ""


def _create_contact(db: Session, *, owner_id: str, phone: str, email: str, name: str, company: str, role: str, source_email_id: int | None) -> CreatedContact:
    # The phone is attached through add_phone below rather than set in the constructor:
    # setting it here would take the contacts-table slot without ever consulting
    # premium_contact_phones, so a number that is somebody else's *secondary* would be
    # claimed anyway and add_phone's refusal would arrive too late to matter.
    contact = PremiumNumberContact(
        owner_id=owner_id, normalized_phone_number=None, display_phone_number="",
        is_recruiter=role == "recruiter", is_employer=role == "employer",
        recruiter_name=name or "Unknown", owner_name=name or "Unknown", company=company or "Unknown",
        first_detected_email_id=source_email_id, source_email_id=source_email_id, created_at=utc_now(), updated_at=utc_now(),
    )
    with db.begin_nested():
        db.add(contact)
        db.flush()
    unclaimed_email = ""
    if email and not attach_email(db, contact, email, role, source_email_id, primary=True):
        unclaimed_email = email
    unclaimed_phone = ""
    if phone and not add_phone(db, contact, phone, primary=True):
        unclaimed_phone = phone
    _action(db, owner_id=owner_id, action_type="create", primary_contact_id=contact.id, source="automatic")
    return CreatedContact(contact, unclaimed_phone, unclaimed_email)


def _review(db: Session, *, owner_id: str, target: PremiumNumberContact, reason: str, phone: str, email: str, source_email_id: int | None, secondary: PremiumNumberContact | None = None, role: str = "recruiter") -> NumberReviewQueue:
    value_filter = NumberReviewQueue.contact_email == email if email else NumberReviewQueue.normalized_phone_number == phone
    existing = db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == owner_id, NumberReviewQueue.target_contact_id == target.id, NumberReviewQueue.reason_code == reason, value_filter, NumberReviewQueue.state.in_(("pending", "dismissed"))).first()
    if existing:
        return existing
    normalized_phone_number = phone or target.normalized_phone_number or ""
    if source_email_id is not None:
        # ux_number_review_queue_owner_phone_email is keyed on (owner, phone, source_email_id)
        # regardless of target/reason - some other review for this exact phone + source email
        # already occupies that slot (e.g. a review's phone got corrected to a number that's
        # also on file elsewhere). Reuse it instead of crashing on insert.
        slot_owner = db.query(NumberReviewQueue).filter(
            NumberReviewQueue.owner_id == owner_id,
            NumberReviewQueue.normalized_phone_number == normalized_phone_number,
            NumberReviewQueue.source_email_id == source_email_id,
        ).first()
        if slot_owner:
            return slot_owner
    row = NumberReviewQueue(
        owner_id=owner_id, source_email_id=source_email_id, target_contact_id=target.id,
        secondary_contact_id=secondary.id if secondary else None, normalized_phone_number=normalized_phone_number,
        display_phone_number=phone or target.display_phone_number or "", owner_name=target.recruiter_name,
        company=target.company, contact_email=email, contact_type=role, role=role,
        reason_code=reason, state="pending", created_at=utc_now(), updated_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def _confirmable(contact: PremiumNumberContact, match, human_confirmed: bool) -> PremiumNumberContact | None:
    """`human_confirmed` says a person typed this in, not that they adjudicated a clash.
    A CONFLICTING match must still come back as None so the caller raises instead of
    letting "Mark as Recruiter" overwrite a contact the classifier just rejected."""
    if not human_confirmed or match.outcome == "conflicting":
        return None
    return contact


def reconcile(db: Session, *, owner_id: str, normalized_phone: str = "", normalized_email: str = "", name: str = "", company: str = "", role: str = "recruiter", source_email_id: int | None = None, human_confirmed: bool = False) -> ReconcileResult:
    phone, email = normalized_phone.strip(), normalize_email(normalized_email)
    resolution = resolve_identity(db, owner_id=owner_id, phone=phone, email=email, role=role)
    by_phone, by_email = resolution.phone_owner, resolution.email_owner
    if resolution.outcome == "none":
        return ReconcileResult(_create_contact(db, owner_id=owner_id, phone=phone, email=email, name=name, company=company, role=role, source_email_id=source_email_id).contact, "created")
    if resolution.outcome == "split":
        review = _review(db, owner_id=owner_id, target=by_phone, secondary=by_email, reason="phone_email_cross_conflict", phone=phone, email=email, source_email_id=source_email_id, role=role)
        # Never resolved to one contact, even for a human: which of the two is right is
        # precisely the question the review card exists to ask.
        return ReconcileResult(None, "pending_merge_approval", review.id)
    if by_phone is not None and by_email is None:
        match = classify_identity_match(by_phone, _candidate(email, name, company, role), role)
        if match.outcome == "confirmed" and email and human_confirmed:
            attach_email(db, by_phone, email, role, source_email_id, primary=False)
            _action(db, owner_id=owner_id, action_type="link_email", primary_contact_id=by_phone.id, value=email, source="human_entry_confirmed")
            return ReconcileResult(by_phone, "confirmed")
        reason = "suggested_email_match" if match.outcome == "confirmed" else "identity_conflict" if match.outcome == "conflicting" else "insufficient_evidence"
        review = _review(db, owner_id=owner_id, target=by_phone, reason=reason, phone=phone, email=email, source_email_id=source_email_id, role=role)
        return ReconcileResult(_confirmable(by_phone, match, human_confirmed), "pending_link_approval", review.id)
    if by_email is not None and by_phone is None:
        match = classify_identity_match(by_email, _candidate(email, name, company, role), role)
        if match.outcome == "confirmed" and phone and human_confirmed:
            add_phone(db, by_email, phone, primary=not bool(by_email.normalized_phone_number))
            _action(db, owner_id=owner_id, action_type="link_phone", primary_contact_id=by_email.id, value=phone, source="human_entry_confirmed")
            return ReconcileResult(by_email, "confirmed")
        if phone:
            reason = "suggested_phone_match" if match.outcome == "confirmed" else "identity_conflict" if match.outcome == "conflicting" else "insufficient_evidence"
            review = _review(db, owner_id=owner_id, target=by_email, reason=reason, phone=phone, email="", source_email_id=source_email_id, role=role)
            return ReconcileResult(_confirmable(by_email, match, human_confirmed), "pending_link_approval", review.id)
        return ReconcileResult(by_email, "confirmed")
    assert by_phone is not None
    name_column = "recruiter_name" if role == "recruiter" else "owner_name"
    for field, value in ((name_column, name), ("company", company)):
        if value and getattr(by_phone, field) in ("", "Unknown"):
            setattr(by_phone, field, value)
    return ReconcileResult(by_phone, "confirmed")


def approve_link(db: Session, review: NumberReviewQueue) -> PremiumNumberContact:
    contact = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == review.target_contact_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if contact is None:
        raise ValueError("Target contact not found")
    role = review.role if review.role in ("recruiter", "employer") else "recruiter"
    if review.contact_email:
        value, action = normalize_email(review.contact_email), "link_email"
        if not attach_email(db, contact, value, role, review.source_email_id, primary=not bool(getattr(contact, headline_email_columns(role)[0]))):
            raise ValueError(f"{value} is already linked to a different contact")
        for model in (RecruiterEmail, AppTSApplication):
            db.query(model).filter(model.owner_id == review.owner_id, model.resolved_recruiter_email == value).update({model.resolved_recruiter_contact_id: contact.id}, synchronize_session=False)
    else:
        value, action = review.normalized_phone_number, "link_phone"
        if not add_phone(db, contact, value, primary=not bool(contact.normalized_phone_number)):
            raise ValueError(f"{value} is already linked to a different contact")
    _action(db, owner_id=review.owner_id, action_type=action, primary_contact_id=contact.id, value=value, source="suggestion_approved")
    review.state = "resolved"
    return contact


def release_contact_claims(db: Session, contact: PremiumNumberContact) -> None:
    """Strip a contact of everything that occupies a unique slot or holds a live FK
    reference, before it's soft-deleted. Neither `ux_premium_number_contacts_owner_phone`
    nor `ux_premium_contact_phones_owner_phone`/`..._emails_owner_email` are scoped by
    deleted_at, so a soft-deleted row still blocks anyone else from claiming its phone/
    email; and active_recruiter_lead_id/active_employer_lead_id can keep pointing at a
    lead whose contact_id has since been reassigned elsewhere (e.g. by a merge), which
    fails a later FK-checked delete of that lead. Deleting a contact must release all of
    it, not just flip a flag.
    """
    db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).delete(synchronize_session=False)
    db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).delete(synchronize_session=False)
    contact.normalized_phone_number = None
    contact.display_phone_number = ""
    contact.phone_extension = ""
    contact.active_recruiter_lead_id = None
    contact.active_employer_lead_id = None


SOFT_DELETE_SNAPSHOT = "soft_delete_snapshot"


def snapshot_contact_claims(db: Session, contact: PremiumNumberContact) -> None:
    """Record every identifier `release_contact_claims` is about to destroy.

    Releasing them is not optional - neither unique constraint is scoped by deleted_at,
    so a soft-deleted contact that kept its phone would block anyone else from ever
    claiming it. But destroying them made delete -> restore silently lossy: a contact came
    back from the Recycle Bin with no phone and no emails. This is what makes it
    reversible.
    """
    payload = {
        "normalized_phone_number": contact.normalized_phone_number or "",
        "phone_extension": contact.phone_extension or "",
        "phones": [
            {"phone": row.normalized_phone_number, "extension": row.phone_extension or "",
             "is_primary": bool(row.is_primary), "label": row.label or ""}
            for row in db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).all()
        ],
        "emails": [
            {"email": row.normalized_email, "role": row.role or "recruiter", "is_primary": bool(row.is_primary)}
            for row in db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).all()
        ],
    }
    _action(db, owner_id=contact.owner_id, action_type=SOFT_DELETE_SNAPSHOT,
            primary_contact_id=contact.id, value=json.dumps(payload, sort_keys=True), source="soft_delete")


def restore_contact_claims(db: Session, contact: PremiumNumberContact) -> list[str]:
    """Re-attach whatever the most recent snapshot recorded. Anything another contact has
    legitimately claimed in the meantime is skipped and returned, so the caller can say so
    rather than pretending the restore was complete."""
    snapshot = db.query(ContactIdentityAction).filter(
        ContactIdentityAction.owner_id == contact.owner_id,
        ContactIdentityAction.action_type == SOFT_DELETE_SNAPSHOT,
        ContactIdentityAction.primary_contact_id == contact.id,
    ).order_by(ContactIdentityAction.created_at.desc(), ContactIdentityAction.id.desc()).first()
    if snapshot is None or not snapshot.value:
        return []
    try:
        payload = json.loads(snapshot.value)
    except ValueError:
        return []
    skipped: list[str] = []
    for entry in payload.get("emails", []):
        email = normalize_email(entry.get("email"))
        if email and not attach_email(db, contact, email, entry.get("role") or "recruiter", None,
                                      primary=bool(entry.get("is_primary"))):
            skipped.append(email)
    entries = list(payload.get("phones", []))
    primary_phone = (payload.get("normalized_phone_number") or "").strip()
    primary_extension = payload.get("phone_extension") or ""
    # The contact's own primary is not guaranteed to have had a matching child row - older
    # contacts predate that mirroring - so it has to be re-added from the scalar too, or a
    # restore silently drops the number the contact was actually reachable at.
    if primary_phone and not any(
        (entry.get("phone") or "") == primary_phone and (entry.get("extension") or "") == primary_extension
        for entry in entries
    ):
        entries.append({"phone": primary_phone, "extension": primary_extension, "is_primary": True})
    # The old primary goes back first so it reclaims the primary slot rather than landing
    # as a secondary behind one of the others.
    ordered = sorted(entries, key=lambda entry: (entry.get("phone") or "") != primary_phone)
    for entry in ordered:
        phone = (entry.get("phone") or "").strip()
        if not phone:
            continue
        wants_primary = phone == payload.get("normalized_phone_number") and (
            entry.get("extension") or "") == (payload.get("phone_extension") or "")
        if not add_phone(db, contact, phone, primary=wants_primary, extension=entry.get("extension") or ""):
            skipped.append(phone)
    return skipped


def _merge_contact_records(
    db: Session, *, owner_id: str, canonical: PremiumNumberContact, loser: PremiumNumberContact, source: str
) -> None:
    for model, value_column in ((PremiumContactEmail, PremiumContactEmail.normalized_email), (PremiumContactPhone, PremiumContactPhone.normalized_phone_number)):
        for child in db.query(model).filter(model.premium_contact_id == loser.id).all():
            duplicate = db.query(model.id).filter(model.premium_contact_id == canonical.id, value_column == getattr(child, value_column.key)).first()
            if duplicate:
                db.delete(child)
            else:
                child.premium_contact_id = canonical.id
    # The session runs with autoflush=False - without this, release_contact_claims'
    # bulk DELETE below (synchronize_session=False, queries the DB directly) doesn't see
    # the in-memory reassignment above and deletes the row out from under it, which then
    # makes the flush's pending UPDATE hit 0 matching rows (StaleDataError).
    db.flush()
    # Version history and any other open/past reviews naming the loser must follow it -
    # otherwise a merge quietly orphans the loser's lead history and leaves stray
    # reviews pointing at a now-deleted contact.
    db.query(PremiumNumberLead).filter(PremiumNumberLead.owner_id == owner_id, PremiumNumberLead.contact_id == loser.id).update({PremiumNumberLead.contact_id: canonical.id}, synchronize_session=False)
    db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == owner_id, NumberReviewQueue.target_contact_id == loser.id).update({NumberReviewQueue.target_contact_id: canonical.id}, synchronize_session=False)
    db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id == owner_id, NumberReviewQueue.secondary_contact_id == loser.id).update({NumberReviewQueue.secondary_contact_id: canonical.id}, synchronize_session=False)
    for model in (RecruiterEmail, AppTSApplication):
        db.query(model).filter(model.owner_id == owner_id, model.resolved_recruiter_contact_id == loser.id).update({model.resolved_recruiter_contact_id: canonical.id}, synchronize_session=False)
    loser_phone = loser.normalized_phone_number
    loser_extension = loser.phone_extension or ""
    # Release the loser's own claim on its phone (and its active-lead pointers) before
    # trying to hand that phone to canonical below - add_phone's own conflict guard would
    # otherwise see the still-live loser as the current owner and refuse the backfill.
    release_contact_claims(db, loser)
    loser.deleted_at = utc_now()
    # The session runs with autoflush=False - without this, add_phone's conflict check
    # below queries the database directly and would still see the loser's own row as the
    # live owner of loser_phone, since the release above only changed it in memory.
    db.flush()
    # Fill-if-blank: a merge shouldn't leave the survivor with no phone at all just
    # because the loser (not the canonical) was the one holding a verified number. A
    # no-extension (direct/desk) number also always wins over one with an extension
    # (company switchboard line), same rule extraction applies when merging numbers in.
    if loser_phone and (loser_phone, loser_extension) != (canonical.normalized_phone_number, canonical.phone_extension or ""):
        becomes_primary = not canonical.normalized_phone_number or (not loser_extension and bool(canonical.phone_extension))
        add_phone(db, canonical, loser_phone, primary=becomes_primary, extension=loser_extension)
    _action(db, owner_id=owner_id, action_type="merge", primary_contact_id=canonical.id, secondary_contact_id=loser.id, source=source)


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
    _merge_contact_records(db, owner_id=review.owner_id, canonical=canonical, loser=loser, source="suggestion_approved")
    review.state = "resolved"
    return canonical


def merge_contacts(
    db: Session, *, owner_id: str, canonical_contact_id: int, loser_contact_id: int, source: str = "manual_merge"
) -> PremiumNumberContact:
    """Merge two contacts a human has identified as duplicates - no review row required."""
    if canonical_contact_id == loser_contact_id:
        raise ValueError("Cannot merge a contact with itself")
    canonical = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id == canonical_contact_id, PremiumNumberContact.deleted_at.is_(None)).first()
    loser = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id == loser_contact_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if canonical is None or loser is None:
        raise ValueError("Merge contact not found")
    _merge_contact_records(db, owner_id=owner_id, canonical=canonical, loser=loser, source=source)
    return canonical


def _evidence_at(db: Session, source_email_id: int | None, fallback, external_opportunity_id: int | None = None):
    if source_email_id is not None:
        email = db.get(RecruiterEmail, source_email_id)
        if email is not None and email.gmail_received_at is not None:
            return email.gmail_received_at
    if external_opportunity_id is not None:
        item = db.get(ExternalOpportunity, external_opportunity_id)
        if item is not None and item.posted_at is not None:
            return item.posted_at
    return fallback


def _latest_phone_evidence_at(db: Session, contact_id: int, phone: str):
    leads = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.contact_id == contact_id, PremiumNumberLead.phone_number_normalized == phone
    ).all()
    if not leads:
        return None
    return max(_evidence_at(db, lead.recruiter_email_id, lead.created_at, lead.external_opportunity_id) for lead in leads)


def _release_contact_phone(db: Session, contact: PremiumNumberContact, phone: str) -> None:
    db.query(PremiumContactPhone).filter(
        PremiumContactPhone.owner_id == contact.owner_id,
        PremiumContactPhone.premium_contact_id == contact.id,
        PremiumContactPhone.normalized_phone_number == phone,
        PremiumContactPhone.phone_extension == "",
    ).delete(synchronize_session=False)
    if contact.normalized_phone_number == phone and not contact.phone_extension:
        contact.normalized_phone_number = None
        contact.display_phone_number = ""
    db.flush()


@dataclass(frozen=True)
class DismissResult:
    contact: PremiumNumberContact
    #: Set when the split-off contact could not take the phone because a third contact
    #: already owns it - a new review naming both claimants, for the reviewer to resolve.
    follow_up_review_id: int | None = None


def dismiss(db: Session, review: NumberReviewQueue) -> DismissResult:
    review.state = "dismissed"
    role = review.role if review.role in ("recruiter", "employer") else "recruiter"
    kwargs = dict(
        owner_id=review.owner_id, email=review.contact_email, name=review.owner_name,
        company=review.company, role=role, source_email_id=review.source_email_id,
    )
    phone = review.normalized_phone_number
    target = None
    if phone and review.target_contact_id:
        target = db.query(PremiumNumberContact).filter(
            PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == review.target_contact_id,
            PremiumNumberContact.deleted_at.is_(None),
        ).first()
    reassigned = False
    if target is not None and target.normalized_phone_number == phone and not target.phone_extension:
        # Two different people share this switchboard number with no extension to tell them
        # apart - whoever's evidence (the real email send time, not when we happened to
        # process it) is more recent keeps the number, instead of whichever contact simply
        # reached the database first. A tie, or no comparable evidence, leaves the existing
        # contact's claim alone.
        target_evidence_at = _latest_phone_evidence_at(db, target.id, phone)
        review_evidence_at = _evidence_at(db, review.source_email_id, review.created_at, review.source_external_opportunity_id)
        # No evidence for the target isn't proof the target is wrong - only override the
        # existing contact's claim when we have something concrete to compare against.
        if target_evidence_at is not None and review_evidence_at > target_evidence_at:
            _release_contact_phone(db, target, review.normalized_phone_number)
            reassigned = True
        else:
            phone = ""
    created = _create_contact(db, phone=phone, **kwargs)
    contact = created.contact
    if created.unclaimed_phone:
        # The number belongs to some third contact the reviewer was never shown. Splitting
        # this person off without it used to happen silently and produced an unfindable,
        # phoneless duplicate; instead, surface the real dispute as a review naming both
        # claimants so it can be resolved through the existing three-way merge UI.
        blocking_id = phone_claim_owner(db, review.owner_id, created.unclaimed_phone, exclude_contact_id=contact.id)
        blocker = db.query(PremiumNumberContact).filter(
            PremiumNumberContact.owner_id == review.owner_id, PremiumNumberContact.id == blocking_id,
            PremiumNumberContact.deleted_at.is_(None),
        ).first() if blocking_id is not None else None
        if blocker is not None:
            follow_up = _review(
                db, owner_id=review.owner_id, target=blocker, secondary=contact, reason="phone_owner_conflict",
                phone=created.unclaimed_phone, email="", source_email_id=review.source_email_id, role=role,
            )
            _action(
                db, owner_id=review.owner_id, action_type="phone_conflict", primary_contact_id=blocker.id,
                secondary_contact_id=contact.id, value=created.unclaimed_phone, source="dismiss_phone_claimed",
            )
            return DismissResult(contact, follow_up.id)
    if reassigned and contact.normalized_phone_number == review.normalized_phone_number:
        _action(
            db, owner_id=review.owner_id, action_type="reassign_phone", primary_contact_id=contact.id,
            secondary_contact_id=target.id, value=review.normalized_phone_number, source="dismiss_evidence_check",
        )
    return DismissResult(contact, None)
