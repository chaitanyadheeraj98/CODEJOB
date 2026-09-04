from __future__ import annotations

import json
from email.utils import parseaddr

from sqlalchemy import func, or_

from app.config import settings
from app.db import SessionLocal
from app.external_feeds.models import ExternalOpportunity
from app.models import (
    Application,
    ApplicationInterview,
    ApplicationRTR,
    ApplicationSuggestion,
    CandidateRecord,
    EmailConversation,
    EmailReplyMessage,
    NumberReviewQueue,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    OpportunitySourceReference,
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
)
from app.services import (
    application_service,
    appts_service,
    field_coverage,
    recruiter_ranking,
    opportunity_lineage_service,
    resume_tracking_service,
)
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES
from app.premium_numbers.phone_normalization import best_display_phone, canonicalize_phone
from app.premium_numbers.domain_guard import (
    is_hidden_invalid_employer_number,
    is_hidden_nvoids_placeholder_recruiter,
)

_VALID_CATEGORIES = ("recruiter", "employer", "review", "lead")


def _fenced(tag: str, value: object) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return f"<{tag}>\n{text}\n</{tag}>"


def _source_reference_payload(db, reference: OpportunitySourceReference) -> dict[str, object]:
    base: dict[str, object] = {
        "source_type": reference.source_type,
        "external_id": reference.external_id,
        "source_url": reference.source_url,
        "first_seen_at": reference.first_seen_at.isoformat(),
        "last_seen_at": reference.last_seen_at.isoformat(),
    }
    try:
        row_id = int(reference.external_id)
    except ValueError:
        return {"source_type": reference.source_type, "external_id": reference.external_id, "status": "missing"}
    if reference.source_type == "gmail":
        row = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == row_id)
            .first()
        )
        if row is None:
            return {"source_type": reference.source_type, "external_id": reference.external_id, "status": "missing"}
        base.update(
            {
                "status": "found",
                "gmail_open_url": row.gmail_message_url,
                "untrusted_source_data": _fenced(
                    "untrusted_source_data",
                    f"Sender: {row.sender}\nSubject: {row.subject}\nRole: {row.role}\n"
                    f"End client: {row.end_client or ''}\nBody summary: {row.body[:2000]}",
                ),
            }
        )
        return base
    row = (
        db.query(ExternalOpportunity)
        .filter(ExternalOpportunity.owner_id == settings.owner_id, ExternalOpportunity.id == row_id)
        .first()
    )
    if row is None:
        return {"source_type": reference.source_type, "external_id": reference.external_id, "status": "missing"}
    base.update(
        {
            "status": "found",
            "external_post_id": row.external_post_id,
            "untrusted_source_data": _fenced(
                "untrusted_source_data",
                f"Role: {row.role}\nCompany: {row.company}\nLocation: {row.location}\n"
                f"Recruiter: {row.recruiter_name} <{row.recruiter_email}>\n"
                f"Body summary: {row.raw_body[:2000]}",
            ),
        }
    )
    return base


def propose_create_premium_contact(
    name: str = "",
    title: str = "",
    company: str = "",
    email: str = "",
    phone: str = "",
    role: str = "recruiter",
) -> dict[str, object]:
    """Validate and shape a Premium Numbers contact proposal without writing it."""
    normalized_role = role.strip().lower()
    normalized_phone = canonicalize_phone(phone)
    missing: list[str] = []
    if not name.strip():
        missing.append("name")
    if not normalized_phone:
        missing.append("phone")
    if normalized_role not in {"recruiter", "employer"}:
        missing.append("role")
    if missing:
        return {"status": "missing_fields", "missing": missing}

    db = SessionLocal()
    try:
        duplicate = (
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == settings.owner_id,
                PremiumNumberContact.normalized_phone_number == normalized_phone,
            )
            .first()
        )
        return {
            "action": "create_premium_contact",
            "fields": {
                "name": name.strip(),
                "title": title.strip(),
                "company": company.strip(),
                "email": email.strip().lower(),
                "phone": phone.strip(),
                "phone_normalized": normalized_phone,
                "phone_display": best_display_phone(phone, fallback=phone),
                "role": normalized_role,
            },
            "duplicate_of_id": duplicate.id if duplicate else None,
        }
    finally:
        db.close()


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
            or_(
                PremiumNumberContact.recruiter_name.ilike(like),
                PremiumNumberContact.company.ilike(like),
                PremiumNumberContact.recruiter_email.ilike(like),
            )
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
            "record_id": row.record_id,
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
    name or company (case-insensitive, partial match); for recruiters this also matches
    their stored email address, so a raw address like "agoyal@webmsi.com" works here too - use
    this when asked to find someone by name or email, since search_candidates does not cover
    this data. If a number was extracted but was
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
        return {
            "count": len(rows),
            "numbers": rows[:capped],
            **_contact_evidence_block(db),
        }
    finally:
        db.close()


# W13. Contact fields the caller sees a value for, and may therefore describe.
# Coverage counts live contacts only - the Recycle Bin is a working queue, not
# an archive of the false, and a binned recruiter must not appear in a
# recommendation about who to contact now. History is available on request; it
# is not the default population.
_CONTACT_REPORTED_FIELDS = [
    "company",
    "recruiter_name",
    "recruiter_email",
    "normalized_phone_number",
    "seen_count",
    "designation",
]

# Reads as fully populated and is not. See `field_coverage.CONTACT_FIELDS`.
_CONTACT_WITHHELD_FIELDS = ["owner_name", "recruiter_verification_level", "is_favorite"]


def _contact_evidence_block(db) -> dict[str, object]:
    """Coverage, refusals and alias warnings that travel with contact rows."""
    block: dict[str, object] = {
        "field_coverage": field_coverage.contact_coverage_for(
            db, _CONTACT_REPORTED_FIELDS, owner_id=settings.owner_id
        ),
        "population": field_coverage.SCOPE_ACTIVE,
        "coverage_note": (
            "Counts active contacts only; Recycle Bin contacts are excluded. Say so "
            "if the answer implies all-time activity. Percentages are corpus-wide "
            "over that population, not over these rows, and a placeholder such as "
            "\"Unknown\" is counted as missing rather than as an answer."
        ),
    }
    refusals = [
        result
        for result in (
            field_coverage.contact_unavailable_result(name)
            for name in _CONTACT_WITHHELD_FIELDS
        )
        if result is not None
    ]
    if refusals:
        block["unavailable_fields"] = refusals
    aliases = [
        note
        for note in (
            field_coverage.unconfirmed_alias_note(
                db,
                name,
                owner_id=settings.owner_id,
                policies=field_coverage.CONTACT_FIELDS,
                model=PremiumNumberContact,
                active_only=True,
            )
            for name in _CONTACT_REPORTED_FIELDS
        )
        if note is not None
    ]
    if aliases:
        block["unconfirmed_aliases"] = aliases
    return block


def rank_recruiters(rule: str = "volume", limit: int = 10, include_deleted: bool = False) -> dict[str, object]:
    """Order recruiters by a stated rule - for "who is worth keeping in touch with".

    Call this for any question asking which recruiters or recruiting companies to
    follow, keep, prioritise or contact first. The answer MUST repeat the returned
    `rule_statement`: it is the definition the ordering used, and the user is
    entitled to disagree with a definition they can see. Never present the order
    as a verdict on who is worth contacting - `other_rules` lists the definitions
    you can re-rank by if they want a different one.

    rule: volume (requirements sent, default), recent (last heard from),
    responsive (has replied), recurring (how often they appear).
    include_deleted: false by default - Recycle Bin contacts are excluded from
    recommendations about who to contact now. Set true only when the user asks
    about all-time history.
    """
    db = SessionLocal()
    try:
        try:
            result = recruiter_ranking.rank_recruiters(
                db,
                owner_id=settings.owner_id,
                rule=rule,
                limit=limit,
                scope=(
                    field_coverage.SCOPE_ALL_TIME
                    if include_deleted
                    else field_coverage.SCOPE_ACTIVE
                ),
            )
        except ValueError as error:
            return {"error": str(error), "rules": sorted(recruiter_ranking.RULES)}
        scope = (
            field_coverage.SCOPE_ALL_TIME if include_deleted else field_coverage.SCOPE_ACTIVE
        )
        result["field_coverage"] = field_coverage.contact_coverage_for(
            db, ["company", "designation", "recruiter_email"], owner_id=settings.owner_id, scope=scope
        )
        # Replies are the sparse signal in this ranking. Reported so an absence is
        # read as "no reply recorded" rather than "this recruiter never answers".
        with_replies = sum(
            1 for row in result["recruiters"] if int(row.get("replies_received") or 0) > 0
        )
        result["signal_notes"] = [
            (
                f"Replies are recorded for {with_replies} of the "
                f"{len(result['recruiters'])} recruiters shown, and for 19 of 497 live "
                "contacts overall. A zero means no reply was matched, not that they "
                "never reply."
            ),
            (
                "Submissions on your behalf are deliberately not part of any rule "
                "here: the application-to-recruiter link is recorded on 2 of 47 "
                "tracked applications, too few to rank on."
            ),
        ]
        return result
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
                    "record_id": row.record_id,
                    "status": row.status,
                    "job_title": row.job_title,
                    "end_client": row.end_client,
                    "location": row.location,
                    "work_mode": row.work_mode,
                    "visa_restrictions": row.visa_restrictions,
                    "domain": row.domain,
                    # `prime_vendor` and `employment_type` are omitted, not blank:
                    # they are empty on every row, so there is no record-level value
                    # to withhold and returning "" invites the absence to be read as
                    # a finding. See `field_coverage` below. `implementation_partner`
                    # is kept because 18 rows genuinely carry one and a lookup on a
                    # named record is legitimate - what it may not support is a claim
                    # about which partners are active.
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
            **_opportunity_evidence_block(db, rows),
        }
    finally:
        db.close()


# Fields the caller sees a value for, and may therefore describe. Coverage is
# measured live against the whole table on every call - never against `rows`,
# which is self-selected and would read far higher than the field deserves.
_REPORTED_FIELDS = [
    "job_title",
    "extracted_skills",
    "work_mode",
    "location",
    "domain",
    "end_client",
    "implementation_partner",
]

# Empty on every row. Omitted from the payload entirely; the refusal explains why.
_WITHHELD_FIELDS = ["prime_vendor", "employment_type"]


def _opportunity_evidence_block(db, rows: list) -> dict[str, object]:
    """Coverage, refusals and alias warnings that travel with the rows.

    The model is not asked to remember how sparse a field is; the number arrives
    beside the data it qualifies. Evidence is harder to argue past than a rule.
    """
    block: dict[str, object] = {
        "field_coverage": field_coverage.coverage_for(
            db, _REPORTED_FIELDS, owner_id=settings.owner_id
        ),
        "coverage_note": (
            "Percentages are corpus-wide over all opportunities, not over these "
            "results. Quote the corpus figure when the answer generalises. Any "
            "count within these rows is a subset figure and must be labelled as "
            "such - it never replaces the corpus figure."
        ),
    }
    refusals = [
        result
        for result in (field_coverage.unavailable_result(name) for name in _WITHHELD_FIELDS)
        if result is not None
    ]
    restricted = [
        result
        for result in (
            field_coverage.unavailable_result(name)
            for name in _REPORTED_FIELDS
            if field_coverage.availability(name) == field_coverage.UNAVAILABLE
        )
        if result is not None
    ]
    if refusals:
        block["unavailable_fields"] = refusals
    if restricted:
        block["restricted_fields"] = restricted
    aliases = [
        note
        for note in (
            field_coverage.unconfirmed_alias_note(db, name, owner_id=settings.owner_id)
            for name in _REPORTED_FIELDS
        )
        if note is not None
    ]
    if aliases:
        block["unconfirmed_aliases"] = aliases
    return block


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _lineage_event_payload(row: OpportunityLifecycleEvent) -> dict[str, object]:
    try:
        metadata: object = json.loads(row.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = row.metadata_json
    return {
        "id": row.id,
        "event_type": row.event_type,
        "occurred_at": row.occurred_at.isoformat(),
        "actor": row.actor,
        "process_name": row.process_name,
        "related_record_type": row.related_record_type,
        "related_record_id": row.related_record_id,
        "note": _fenced("untrusted_event_data", row.note),
        "metadata": _fenced("untrusted_event_data", metadata),
    }


def _lineage_opportunity_payload(db, row: RecruiterOpportunity | None) -> dict[str, object] | None:
    if row is None:
        return None
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == row.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    return {
        "id": row.id,
        "status": row.status,
        "source_type": row.source_type,
        "source_email_id": row.source_email_id,
        "external_opportunity_id": row.external_opportunity_id,
        "source_url": row.source_url,
        "job_title": row.job_title,
        "end_client": row.end_client,
        "location": row.location,
        "work_mode": row.work_mode,
        "visa_restrictions": row.visa_restrictions,
        "employment_type": row.employment_type,
        "rate_amount": row.rate_amount,
        "rate_currency": row.rate_currency,
        "rate_unit": row.rate_unit,
        "contract_duration": row.contract_duration,
        "implementation_partner": row.implementation_partner,
        "prime_vendor": row.prime_vendor,
        "domain": row.domain,
        "resume_file_name": row.resume_file_name,
        "extracted_skills": row.extracted_skills,
        "received_at": _iso(row.received_at),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "contact": (
            {
                "id": contact.id,
                "name": contact.recruiter_name,
                "company": contact.company,
                "designation": contact.designation,
                "email": contact.recruiter_email,
                "phone_display": contact.display_phone_number,
            }
            if contact
            else None
        ),
        "untrusted_opportunity_data": _fenced(
            "untrusted_opportunity_data",
            f"Email subject: {row.email_subject}\nEmail sender: {row.email_sender}\n"
            f"Evidence: {row.evidence}\nNotes: {row.notes}",
        ),
    }


def _lineage_application_payload(
    db,
    row,
    *,
    models: application_service.ApplicationModels,
) -> dict[str, object]:
    rtr_history = (
        db.query(models.rtr_cls)
        .filter(
            models.rtr_cls.owner_id == settings.owner_id,
            models.rtr_cls.application_id == row.id,
        )
        .order_by(models.rtr_cls.requested_at.desc(), models.rtr_cls.id.desc())
        .all()
    )
    interviews = (
        db.query(models.interview_cls)
        .filter(
            models.interview_cls.owner_id == settings.owner_id,
            models.interview_cls.application_id == row.id,
            models.interview_cls.deleted_at.is_(None),
        )
        .order_by(models.interview_cls.created_at.asc(), models.interview_cls.id.asc())
        .all()
    )
    suggestions = (
        db.query(models.suggestion_cls)
        .filter(
            models.suggestion_cls.owner_id == settings.owner_id,
            models.suggestion_cls.application_id == row.id,
        )
        .order_by(models.suggestion_cls.created_at.desc(), models.suggestion_cls.id.desc())
        .all()
    )
    return {
        "family": models.related_record_type,
        "id": row.id,
        "resume_asset_id": row.resume_asset_id,
        "resume_version_snapshot": row.resume_version_snapshot,
        "resume_file_name_snapshot": row.resume_file_name_snapshot,
        "status": row.status,
        "status_changed_at": row.status_changed_at.isoformat(),
        "resume_shared_at": _iso(row.resume_shared_at),
        "submitted_to_client_at": _iso(row.submitted_to_client_at),
        "next_action_type": row.next_action_type,
        "next_action_at": _iso(row.next_action_at),
        "closed_at": _iso(row.closed_at),
        "closed_reason": row.closed_reason,
        "closed_reason_code": row.closed_reason_code,
        "created_at": row.created_at.isoformat(),
        "resume": {
            "resume_asset_id": row.resume_asset_id,
            "resume_version_snapshot": row.resume_version_snapshot,
            "resume_file_name_snapshot": row.resume_file_name_snapshot,
            "performance_scope": "owner-wide across every submission using this resume",
            "performance": resume_tracking_service.combined_resume_funnel_metrics(
                db,
                owner_id=settings.owner_id,
                resume_asset_id=row.resume_asset_id,
            ),
        },
        "rtr_history": [
            {
                "id": rtr.id,
                "status": rtr.status,
                "role_scope": rtr.role_scope,
                "end_client_scope": rtr.end_client_scope,
                "requested_at": rtr.requested_at.isoformat(),
                "confirmed_at": _iso(rtr.confirmed_at),
                "expires_at": _iso(rtr.expires_at),
                "proof_attachment_id": rtr.proof_attachment_id,
                "proof_recruiter_email_id": rtr.proof_recruiter_email_id,
            }
            for rtr in rtr_history
        ],
        "interviews": [
            {
                "id": interview.id,
                "round_type": interview.round_type,
                "scheduled_at": _iso(interview.scheduled_at),
                "format": interview.format,
                "interviewer_names": interview.interviewer_names,
                "feedback": _fenced("untrusted_event_data", interview.feedback),
                "result": interview.result,
                "follow_up_task_note": interview.follow_up_task_note,
            }
            for interview in interviews
        ],
        "suggestions": [
            {
                "id": suggestion.id,
                "suggestion_type": suggestion.suggestion_type,
                "status": suggestion.status,
                "confidence": suggestion.confidence,
                "suggested_status": suggestion.suggested_status,
                "suggested_next_action_type": suggestion.suggested_next_action_type,
                "suggested_next_action_at": _iso(suggestion.suggested_next_action_at),
                "reason": _fenced("untrusted_event_data", suggestion.reason),
                "created_at": suggestion.created_at.isoformat(),
                "resolved_at": _iso(suggestion.resolved_at),
            }
            for suggestion in suggestions
        ],
    }


def _record_application_payloads(
    db,
    *,
    record_id: str,
    historical_opportunity_id: int | None = None,
) -> list[dict[str, object]]:
    legacy = [
        row
        for row in opportunity_lineage_service.applications_for_record(
            db,
            owner_id=settings.owner_id,
            record_id=record_id,
            model=Application,
        )
        if row.promoted_to_appts_application_id is None
    ]
    current = opportunity_lineage_service.applications_for_record(
        db,
        owner_id=settings.owner_id,
        record_id=record_id,
        model=appts_service.APPTS_MODELS.application_cls,
    )
    if historical_opportunity_id is not None:
        legacy_ids = {row.id for row in legacy}
        legacy.extend(
            row
            for row in db.query(Application).filter(
                Application.owner_id == settings.owner_id,
                Application.recruiter_opportunity_id == historical_opportunity_id,
                Application.deleted_at.is_(None),
                Application.promoted_to_appts_application_id.is_(None),
            ).all()
            if row.id not in legacy_ids
        )
        current_ids = {row.id for row in current}
        current.extend(
            row
            for row in db.query(appts_service.APPTS_MODELS.application_cls).filter(
                appts_service.APPTS_MODELS.application_cls.owner_id == settings.owner_id,
                appts_service.APPTS_MODELS.application_cls.recruiter_opportunity_id == historical_opportunity_id,
                appts_service.APPTS_MODELS.application_cls.deleted_at.is_(None),
            ).all()
            if row.id not in current_ids
        )
    return [
        *[
            _lineage_application_payload(db, row, models=application_service.LEGACY_MODELS)
            for row in legacy
        ],
        *[
            _lineage_application_payload(db, row, models=appts_service.APPTS_MODELS)
            for row in current
        ],
    ]


def _candidate_summary(db, record: CandidateRecord) -> dict[str, object]:
    """Resolve a CandidateRecord to its underlying RecruiterEmail or ExternalOpportunity
    row (whichever exists - see CandidateRecord's docstring), untrusted-fenced the same
    way _source_reference_payload fences a lineage source reference."""
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == record.owner_id, RecruiterEmail.record_id == record.id)
        .order_by(RecruiterEmail.created_at.asc())
        .first()
    )
    if email is not None:
        return {
            "source_type": "gmail",
            "email_id": email.id,
            "state": email.state,
            "decision": email.decision,
            "score": email.score,
            "ats_score": email.ats_score,
            "sendability_status": email.sendability_status,
            "resume_asset_id": email.resume_asset_id,
            "resume_file_name": email.resume_file_name,
            "gmail_open_url": email.gmail_message_url,
            "untrusted_candidate_data": _fenced(
                "untrusted_candidate_data",
                f"Sender: {email.sender}\nSubject: {email.subject}\nRole: {email.role}\n"
                f"Location: {email.location}\nEnd client: {email.end_client or ''}\n"
                f"Body summary: {email.body[:2000]}",
            ),
        }
    external = (
        db.query(ExternalOpportunity)
        .filter(ExternalOpportunity.owner_id == record.owner_id, ExternalOpportunity.record_id == record.id)
        .first()
    )
    if external is not None:
        return {
            "source_type": "nvoids",
            "external_opportunity_id": external.id,
            "bridge_status": external.bridge_status,
            "external_post_id": external.external_post_id,
            "source_url": external.source_url,
            "untrusted_candidate_data": _fenced(
                "untrusted_candidate_data",
                f"Role: {external.role}\nCompany: {external.company}\nLocation: {external.location}\n"
                f"Recruiter: {external.recruiter_name} <{external.recruiter_email}>\n"
                f"Body summary: {external.raw_body[:2000]}",
            ),
        }
    return {"status": "missing"}


def _email_activity(db, *, owner_id: str, record_id: str) -> dict[str, object]:
    """Who this Record's resume was sent to and which reply threads exist, keyed off
    RecruiterEmail.record_id alone - works whether or not the record has an opportunity."""
    emails = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.record_id == record_id)
        .all()
    )
    email_ids = [e.id for e in emails]
    conversations = (
        db.query(EmailConversation)
        .filter(EmailConversation.owner_id == owner_id, EmailConversation.root_recruiter_email_id.in_(email_ids))
        .all()
    ) if email_ids else []
    conv_ids = [c.id for c in conversations]
    # Ordered desc so the first message seen per conversation_id, below, is that
    # conversation's latest inbound reply - avoids a second query/subquery for it.
    inbound = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.conversation_id.in_(conv_ids),
            EmailReplyMessage.direction == "inbound",
        )
        .order_by(EmailReplyMessage.received_at.desc(), EmailReplyMessage.id.desc())
        .all()
    ) if conv_ids else []
    reply_counts: dict[int, int] = {}
    latest_inbound: dict[int, EmailReplyMessage] = {}
    for message in inbound:
        reply_counts[message.conversation_id] = reply_counts.get(message.conversation_id, 0) + 1
        latest_inbound.setdefault(message.conversation_id, message)
    return {
        "sent_to": [
            {
                "email_id": e.id,
                "recipient_email": e.recipient_email,
                "cc_email": e.cc_email,
                "sent_status": e.sent_status,
                "sent_at": e.sent_at.isoformat() if e.sent_at else None,
            }
            for e in emails
        ],
        "threads": [
            {
                "conversation_id": c.id,
                "root_email_id": c.root_recruiter_email_id,
                "status": c.status,
                "last_message_at": c.last_message_at.isoformat(),
                "unread_reply_count": c.unread_reply_count,
                "inbound_reply_count": reply_counts.get(c.id, 0),
                "latest_inbound_reply_at": (
                    latest_inbound[c.id].received_at.isoformat() if c.id in latest_inbound else None
                ),
                "latest_inbound_reply": (
                    {
                        "sender": latest_inbound[c.id].sender,
                        "untrusted_reply_data": _fenced(
                            "untrusted_recruiter_reply", latest_inbound[c.id].body[:2000]
                        ),
                    }
                    if c.id in latest_inbound else None
                ),
            }
            for c in conversations
        ],
    }


def _lineage_detail_payload(
    db,
    lineage: OpportunityLineage,
    event_limit: int,
    *,
    record_id: str,
) -> dict[str, object]:
    references = (
        db.query(OpportunitySourceReference)
        .filter(
            OpportunitySourceReference.owner_id == settings.owner_id,
            OpportunitySourceReference.lineage_id == lineage.id,
        )
        .order_by(OpportunitySourceReference.first_seen_at.asc(), OpportunitySourceReference.id.asc())
        .all()
    )
    event_query = db.query(OpportunityLifecycleEvent).filter(
        OpportunityLifecycleEvent.owner_id == settings.owner_id,
        OpportunityLifecycleEvent.lineage_id == lineage.id,
    )
    tracked_opportunity_id = lineage.recruiter_opportunity_id
    if tracked_opportunity_id is None:
        historical_opportunity_event = (
            event_query.filter(
                OpportunityLifecycleEvent.related_record_type
                == "RecruiterOpportunity",
                OpportunityLifecycleEvent.related_record_id.is_not(None),
            )
            .order_by(
                OpportunityLifecycleEvent.occurred_at.desc(),
                OpportunityLifecycleEvent.id.desc(),
            )
            .first()
        )
        if historical_opportunity_event is not None:
            tracked_opportunity_id = historical_opportunity_event.related_record_id
    total_event_count = event_query.count()
    events = (
        event_query.order_by(
            OpportunityLifecycleEvent.occurred_at.desc(),
            OpportunityLifecycleEvent.id.desc(),
        )
        .limit(max(1, min(event_limit, 200)))
        .all()
    )
    opportunity = None
    if tracked_opportunity_id is not None:
        opportunity = (
            db.query(RecruiterOpportunity)
            .filter(
                RecruiterOpportunity.owner_id == settings.owner_id,
                RecruiterOpportunity.id == tracked_opportunity_id,
            )
            .first()
        )
    return {
        # was_promoted answers "did this lineage ever become a real RecruiterOpportunity" -
        # true even after that opportunity row was later deleted (tracked_opportunity_id's
        # historical-event fallback above), false for a lineage that only ever sat in review.
        # get_record_details pops this before returning the payload to a caller.
        "was_promoted": tracked_opportunity_id is not None,
        "lineage_id": lineage.id,
        "origin_type": lineage.origin_type,
        "current_status": lineage.current_status,
        "created_at": lineage.created_at.isoformat(),
        "closed_at": _iso(lineage.closed_at),
        "source_references": [_source_reference_payload(db, row) for row in references],
        "total_event_count": total_event_count,
        "events": [_lineage_event_payload(row) for row in events],
        "recruiter_opportunity": _lineage_opportunity_payload(db, opportunity),
        "applications": _record_application_payloads(
            db,
            record_id=record_id,
            historical_opportunity_id=tracked_opportunity_id,
        ),
    }


def get_record_details(record_id: str, event_limit: int = 50) -> dict[str, object]:
    """Get one owner-scoped candidate Record, both application-table families, shared
    outcomes, email activity, and any recorded opportunity lifecycle.

    has_opportunity is False for a Record that hasn't been converted into a recruiter
    opportunity yet; say so plainly rather than guessing at recruiter/application details
    in that case. email_activity (who this candidate's resume was sent to, and reply
    threads) is populated either way. Lifecycle events are capped and returned
    most-recent-first; every other section is complete. Each application's resume
    performance is owner-wide across every submission using that resume, not scoped to
    this Record.
    """
    db = SessionLocal()
    try:
        record = opportunity_lineage_service.get_record(db, owner_id=settings.owner_id, record_id=record_id)
        if record is None:
            return {"error": "Record not found"}
        candidate = _candidate_summary(db, record)
        email_activity = _email_activity(db, owner_id=settings.owner_id, record_id=record.id)
        lineage = (
            db.query(OpportunityLineage).filter(OpportunityLineage.id == record.internal_lineage_id).first()
            if record.internal_lineage_id
            else None
        )
        base = {
            "record_id": record.id,
            "origin_type": record.origin_type,
            "created_at": record.created_at.isoformat(),
            "candidate": candidate,
            "email_activity": email_activity,
            "applications": _record_application_payloads(db, record_id=record.id),
            "outcomes": opportunity_lineage_service.record_outcomes(
                db,
                owner_id=settings.owner_id,
                record_id=record.id,
            ),
        }
        # A lineage can exist before promotion too (e.g. a pending review-queue card already
        # has one, unchanged from the shipped 20260825_0031 behavior), and one that WAS
        # promoted can later lose its recruiter_opportunity_id if that row gets deleted (see
        # detach_recruiter_opportunity_for_deletion) while still keeping its history. Either
        # way, has_opportunity must reflect whether this ever became a real opportunity - not
        # merely "a lineage exists" or "still has a live recruiter_opportunity_id today".
        if lineage is None:
            return {**base, "has_opportunity": False}
        detail = _lineage_detail_payload(db, lineage, event_limit, record_id=record.id)
        was_promoted = detail.pop("was_promoted")
        if not was_promoted:
            return {**base, "has_opportunity": False}
        return {**detail, **base, "applications": detail["applications"], "has_opportunity": True}
    finally:
        db.close()
