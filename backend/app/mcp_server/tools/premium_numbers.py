from __future__ import annotations

from email.utils import parseaddr

from sqlalchemy import func, or_

from app.config import settings
from app.db import SessionLocal
from app.models import (
    NumberReviewQueue,
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
)
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES
from app.premium_numbers.domain_guard import (
    is_hidden_invalid_employer_number,
    is_hidden_nvoids_placeholder_recruiter,
)

_VALID_CATEGORIES = ("recruiter", "employer", "review", "lead")


def _recruiter_rows(db, email_id: int, recruiter_email_hint: str, name_search: str) -> list[dict[str, object]]:
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.is_recruiter.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    )
    if email_id:
        # A premium contact is deduped globally by phone number, so the row that first captured
        # a recruiter's number is often attached to an earlier email, not this specific thread.
        # Match on the recruiter's address too, not just first_detected_email_id, or a recruiter
        # who replied on a later thread would wrongly show up as having no stored number.
        conditions = [PremiumNumberContact.first_detected_email_id == email_id]
        if recruiter_email_hint:
            conditions.append(func.lower(PremiumNumberContact.recruiter_email) == recruiter_email_hint)
        query = query.filter(or_(*conditions))
    if name_search:
        like = f"%{name_search}%"
        query = query.filter(
            or_(PremiumNumberContact.recruiter_name.ilike(like), PremiumNumberContact.company.ilike(like))
        )
    return [
        {
            "category": "recruiter_number",
            "is_confirmed": True,
            "id": row.id,
            "phone_display": row.display_phone_number,
            "name": row.recruiter_name,
            "company": row.company,
            "designation": row.designation,
            "recruiter_email": row.recruiter_email,
            "confidence": None,
            "source_email_id": row.first_detected_email_id,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in query.order_by(PremiumNumberContact.updated_at.desc())
        if not is_hidden_nvoids_placeholder_recruiter(row)
    ]


def _employer_rows(db, email_id: int, _recruiter_email_hint: str, name_search: str) -> list[dict[str, object]]:
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.is_employer.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    )
    if email_id:
        query = query.filter(PremiumNumberContact.source_email_id == email_id)
    if name_search:
        like = f"%{name_search}%"
        query = query.filter(or_(PremiumNumberContact.owner_name.ilike(like), PremiumNumberContact.company.ilike(like)))
    return [
        {
            "category": "employer_number",
            "is_confirmed": True,
            "id": row.id,
            "phone_display": row.display_phone_number,
            "name": row.owner_name,
            "company": row.company,
            "designation": None,
            "recruiter_email": None,
            "confidence": None,
            "source_email_id": row.source_email_id,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in query.order_by(PremiumNumberContact.updated_at.desc())
        if not is_hidden_invalid_employer_number(row)
    ]


def _review_rows(db, email_id: int, _recruiter_email_hint: str, name_search: str) -> list[dict[str, object]]:
    query = db.query(NumberReviewQueue).filter(
        NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.state == "pending"
    )
    if email_id:
        query = query.filter(NumberReviewQueue.source_email_id == email_id)
    if name_search:
        like = f"%{name_search}%"
        query = query.filter(or_(NumberReviewQueue.owner_name.ilike(like), NumberReviewQueue.company.ilike(like)))
    return [
        {
            "category": "pending_review",
            "is_confirmed": False,
            "id": row.id,
            "phone_display": row.display_phone_number,
            "name": row.owner_name,
            "company": row.company,
            "designation": row.designation,
            "recruiter_email": None,
            "confidence": row.confidence,
            "source_email_id": row.source_email_id,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in query.order_by(NumberReviewQueue.updated_at.desc())
    ]


def _lead_rows(db, email_id: int, _recruiter_email_hint: str, name_search: str) -> list[dict[str, object]]:
    query = db.query(PremiumNumberLead).filter(PremiumNumberLead.owner_id == settings.owner_id)
    if email_id:
        query = query.filter(PremiumNumberLead.recruiter_email_id == email_id)
    else:
        query = query.filter(PremiumNumberLead.is_recruiter_relevant.is_(True))
    if name_search:
        like = f"%{name_search}%"
        query = query.filter(or_(PremiumNumberLead.owner_name.ilike(like), PremiumNumberLead.company.ilike(like)))
    return [
        {
            "category": "extracted_lead",
            "is_confirmed": False,
            "id": row.id,
            "phone_display": row.phone_number_display,
            "name": row.owner_name,
            "company": row.company,
            "designation": row.designation,
            "recruiter_email": None,
            "confidence": row.confidence,
            "source_email_id": row.recruiter_email_id,
            "updated_at": row.updated_at.isoformat(),
        }
        for row in query.order_by(
            PremiumNumberLead.recruiter_relevance_score.desc(), PremiumNumberLead.updated_at.desc()
        )
    ]


_CATEGORY_LOADERS = {
    "recruiter": _recruiter_rows,
    "employer": _employer_rows,
    "review": _review_rows,
    "lead": _lead_rows,
}


def list_contact_numbers(category: str = "", email_id: int = 0, name: str = "", limit: int = 10) -> dict[str, object]:
    """List phone numbers captured from recruiter/employer emails (the Premium Numbers feature).

    category: "recruiter" (confirmed recruiter numbers), "employer" (confirmed employer
    numbers), "review" (pending, not yet confirmed), or "lead" (raw extraction, unconfirmed).
    Omit category to search all four. Pass email_id (a candidate/email id from
    search_candidates or get_recruiter_replies) to find the number(s) tied to one specific
    email instead of browsing everything. Pass name to search by a recruiter's or employer's
    name or company (case-insensitive, partial match) - use this when asked to find someone by
    name, since search_candidates does not cover this data. If a number was extracted but was
    junk/invalid, it is intentionally left out here, same as in the app's UI; say the number is
    unavailable rather than guessing one.
    """
    normalized_category = category.strip().lower()
    if normalized_category and normalized_category not in _VALID_CATEGORIES:
        return {"error": f"Unknown category '{category}'. Valid values: {', '.join(_VALID_CATEGORIES)}."}
    name_search = name.strip()

    db = SessionLocal()
    try:
        recruiter_email_hint = ""
        if email_id:
            root = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
                .first()
            )
            if root is not None:
                _, address = parseaddr(root.sender or "")
                recruiter_email_hint = address.lower()

        loaders = (
            [_CATEGORY_LOADERS[normalized_category]] if normalized_category else list(_CATEGORY_LOADERS.values())
        )
        rows: list[dict[str, object]] = []
        for loader in loaders:
            rows.extend(loader(db, email_id, recruiter_email_hint, name_search))
        rows.sort(key=lambda row: row["updated_at"], reverse=True)
        capped = max(1, min(limit, 25))
        return {"count": len(rows), "numbers": rows[:capped]}
    finally:
        db.close()


def list_recruiter_opportunities(status: str = "", source_email_id: int = 0, limit: int = 10) -> dict[str, object]:
    """List the owner's recruiter opportunities (job leads tied to a confirmed recruiter number).

    status: one of New/Called/Applied/Follow Up/Closed/Not Interested; omit for all.
    source_email_id: the "Email ID" shown on the card in the UI - matches a Gmail thread id,
    or for Nvoids-sourced cards, the external posting id (the UI shows both under one label).
    """
    db = SessionLocal()
    try:
        query = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == settings.owner_id)
        if status:
            if status not in OPPORTUNITY_STATUS_VALUES:
                return {
                    "error": f"Unknown status '{status}'. Valid values: {', '.join(sorted(OPPORTUNITY_STATUS_VALUES))}."
                }
            query = query.filter(RecruiterOpportunity.status == status)
        if source_email_id:
            query = query.filter(
                or_(
                    RecruiterOpportunity.source_email_id == source_email_id,
                    RecruiterOpportunity.external_opportunity_id == source_email_id,
                )
            )

        total = query.count()
        rows = (
            query.order_by(RecruiterOpportunity.received_at.desc(), RecruiterOpportunity.created_at.desc())
            .limit(max(1, min(limit, 25)))
            .all()
        )

        recruiter_ids = sorted({row.recruiter_number_id for row in rows})
        recruiters = (
            {
                row.id: row
                for row in db.query(PremiumNumberContact).filter(
                    PremiumNumberContact.owner_id == settings.owner_id,
                    PremiumNumberContact.id.in_(recruiter_ids),
                    PremiumNumberContact.is_recruiter.is_(True),
                    PremiumNumberContact.deleted_at.is_(None),
                )
            }
            if recruiter_ids
            else {}
        )

        return {
            "count": total,
            "opportunities": [
                {
                    "id": row.id,
                    "status": row.status,
                    "job_title": row.job_title,
                    "end_client": row.end_client,
                    "location": row.location,
                    "work_mode": row.work_mode,
                    "visa_restrictions": row.visa_restrictions,
                    "domain": row.domain,
                    "prime_vendor": row.prime_vendor,
                    "implementation_partner": row.implementation_partner,
                    "resume_file_name": row.resume_file_name,
                    "extracted_skills": row.extracted_skills,
                    "received_at": row.received_at.isoformat() if row.received_at else None,
                    "source_email_id": row.source_email_id,
                    "email_id": row.source_email_id or row.external_opportunity_id,
                    "recruiter_name": recruiters[row.recruiter_number_id].recruiter_name
                    if row.recruiter_number_id in recruiters
                    else "",
                    "recruiter_email": recruiters[row.recruiter_number_id].recruiter_email
                    if row.recruiter_number_id in recruiters
                    else "",
                    "recruiter_phone": recruiters[row.recruiter_number_id].display_phone_number
                    if row.recruiter_number_id in recruiters
                    else "",
                    "untrusted_opportunity_data": (
                        "<untrusted_opportunity_data>\n"
                        f"Email subject: {row.email_subject}\nEmail sender: {row.email_sender}\n"
                        f"Evidence: {row.evidence}\nNotes: {row.notes}\n"
                        "</untrusted_opportunity_data>"
                    ),
                }
                for row in rows
            ],
        }
    finally:
        db.close()
