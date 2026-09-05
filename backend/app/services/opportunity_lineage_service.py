from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import (
    AppTSApplication,
    AppTSApplicationInterview,
    Application,
    ApplicationInterview,
    CandidateRecord,
    EmailConversation,
    EmailOpenEvent,
    EmailReplyMessage,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    OpportunitySourceReference,
    RecruiterEmail,
    RecruiterOpportunity,
    utc_now,
)


logger = logging.getLogger(__name__)

# The ingestion paths a candidate record can originate from. "manual" is a
# requirement the user pasted in - a third path beside the Gmail sweep and the
# Nvoids feed, for requirements that arrive on WhatsApp or anywhere else the
# application cannot read. A plain String(20) column, so widening the set is
# additive: existing rows and every check against them are unaffected.
SOURCE_TYPES = {"gmail", "nvoids", "manual"}


def _validation_error(message: str) -> ValueError:
    # Local import avoids an import cycle when application_service mirrors its
    # events into this module.
    from app.services.application_service import ApplicationValidationError

    return ApplicationValidationError(message)


def _resolve_source_row(
    db: Session,
    *,
    owner_id: str,
    source_type: str,
    external_id: str,
) -> RecruiterEmail | ExternalOpportunity | None:
    if source_type not in SOURCE_TYPES:
        raise _validation_error("Invalid opportunity source type")
    normalized_id = str(external_id or "").strip()
    if not normalized_id:
        return None
    try:
        row_id = int(normalized_id)
    except ValueError:
        return None
    model = RecruiterEmail if source_type == "gmail" else ExternalOpportunity
    return db.query(model).filter(model.owner_id == owner_id, model.id == row_id).first()


def _source_reference(
    db: Session,
    *,
    lineage_id: str,
    source_type: str,
    external_id: str,
) -> OpportunitySourceReference | None:
    return (
        db.query(OpportunitySourceReference)
        .filter(
            OpportunitySourceReference.lineage_id == lineage_id,
            OpportunitySourceReference.source_type == source_type,
            OpportunitySourceReference.external_id == external_id,
        )
        .first()
    )


def create_lineage(
    db: Session,
    *,
    owner_id: str,
    origin_type: str,
    source_type: str,
    external_id: str,
    source_url: str,
    process_name: str,
    recruiter_opportunity_id: int | None = None,
) -> OpportunityLineage:
    if origin_type not in SOURCE_TYPES:
        raise _validation_error("Invalid opportunity origin type")
    source_row = _resolve_source_row(
        db,
        owner_id=owner_id,
        source_type=source_type,
        external_id=external_id,
    )
    lineage = OpportunityLineage(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        origin_type=origin_type,
        recruiter_opportunity_id=recruiter_opportunity_id,
        current_status="active",
    )
    db.add(lineage)
    db.flush()
    if source_row is not None:
        attach_source_reference(
            db,
            lineage_id=lineage.id,
            source_type=source_type,
            external_id=external_id,
            source_url=source_url,
        )
    record_event(
        db,
        lineage_id=lineage.id,
        event_type="ingested",
        process_name=process_name,
        related_record_type=(
            "RecruiterOpportunity" if recruiter_opportunity_id is not None else "NumberReviewQueue"
        ),
        related_record_id=recruiter_opportunity_id,
    )
    return lineage


def attach_source_reference(
    db: Session,
    *,
    lineage_id: str,
    source_type: str,
    external_id: str,
    source_url: str,
) -> OpportunitySourceReference | None:
    lineage = db.get(OpportunityLineage, lineage_id)
    if lineage is None:
        raise _validation_error("Opportunity lineage not found")
    normalized_external_id = str(external_id or "").strip()
    if not normalized_external_id:
        return None
    if _resolve_source_row(
        db,
        owner_id=lineage.owner_id,
        source_type=source_type,
        external_id=normalized_external_id,
    ) is None:
        return None

    existing = _source_reference(
        db,
        lineage_id=lineage_id,
        source_type=source_type,
        external_id=normalized_external_id,
    )
    now = utc_now()
    if existing is not None:
        existing.last_seen_at = now
        if source_url:
            existing.source_url = source_url
        return existing

    reference = OpportunitySourceReference(
        owner_id=lineage.owner_id,
        lineage_id=lineage_id,
        source_type=source_type,
        external_id=normalized_external_id,
        source_url=source_url or "",
        first_seen_at=now,
        last_seen_at=now,
    )
    try:
        with db.begin_nested():
            db.add(reference)
            db.flush()
    except IntegrityError:
        existing = _source_reference(
            db,
            lineage_id=lineage_id,
            source_type=source_type,
            external_id=normalized_external_id,
        )
        if existing is None:
            raise
        existing.last_seen_at = now
        if source_url:
            existing.source_url = source_url
        return existing
    return reference


def attach_recruiter_opportunity(
    db: Session,
    *,
    lineage_id: str,
    recruiter_opportunity_id: int,
    process_name: str = "main_api",
) -> OpportunityLineage:
    lineage = db.get(OpportunityLineage, lineage_id)
    if lineage is None:
        raise _validation_error("Opportunity lineage not found")
    if lineage.recruiter_opportunity_id is not None:
        raise _validation_error("Opportunity lineage is already promoted")
    lineage.recruiter_opportunity_id = recruiter_opportunity_id
    record_event(
        db,
        lineage_id=lineage.id,
        event_type="promoted",
        process_name=process_name,
        related_record_type="RecruiterOpportunity",
        related_record_id=recruiter_opportunity_id,
    )
    return lineage


def record_event(
    db: Session,
    *,
    lineage_id: str,
    event_type: str,
    actor: str = "system",
    process_name: str = "",
    related_record_type: str = "",
    related_record_id: int | None = None,
    note: str = "",
    metadata: dict[str, object] | None = None,
) -> OpportunityLifecycleEvent:
    lineage = db.get(OpportunityLineage, lineage_id)
    if lineage is None:
        raise _validation_error("Opportunity lineage not found")
    now = utc_now()
    event = OpportunityLifecycleEvent(
        owner_id=lineage.owner_id,
        lineage_id=lineage.id,
        event_type=event_type,
        occurred_at=now,
        actor=actor,
        process_name=process_name,
        related_record_type=related_record_type,
        related_record_id=related_record_id,
        note=note,
        metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        created_at=now,
    )
    db.add(event)
    return event


def get_lineage_for_opportunity(
    db: Session,
    *,
    owner_id: str,
    recruiter_opportunity_id: int,
) -> OpportunityLineage | None:
    return (
        db.query(OpportunityLineage)
        .filter(
            OpportunityLineage.owner_id == owner_id,
            OpportunityLineage.recruiter_opportunity_id == recruiter_opportunity_id,
        )
        .first()
    )


def detach_recruiter_opportunity_for_deletion(
    db: Session,
    *,
    owner_id: str,
    recruiter_opportunity_id: int,
    process_name: str,
) -> OpportunityLineage | None:
    lineage = get_lineage_for_opportunity(
        db,
        owner_id=owner_id,
        recruiter_opportunity_id=recruiter_opportunity_id,
    )
    if lineage is None:
        return None
    record_event(
        db,
        lineage_id=lineage.id,
        event_type="deleted",
        process_name=process_name,
        related_record_type="RecruiterOpportunity",
        related_record_id=recruiter_opportunity_id,
    )
    lineage.current_status = "closed"
    lineage.closed_at = utc_now()
    lineage.recruiter_opportunity_id = None
    return lineage


def create_candidate_record(db: Session, *, owner_id: str, origin_type: str) -> CandidateRecord:
    """The Record ID anchor-point function. Called once per candidate at RecruiterEmail
    (Gmail) or ExternalOpportunity (Nvoids) creation."""
    if origin_type not in SOURCE_TYPES:
        raise _validation_error("Invalid candidate record origin type")
    record = CandidateRecord(id=str(uuid.uuid4()), owner_id=owner_id, origin_type=origin_type)
    db.add(record)
    db.flush()
    return record


def resolve_record_id(db: Session, *, owner_id: str, source_type: str, external_id: str) -> str | None:
    """Reads the record_id already stamped on a RecruiterEmail/ExternalOpportunity row."""
    normalized_id = str(external_id or "").strip()
    if not normalized_id:
        return None
    try:
        row_id = int(normalized_id)
    except ValueError:
        return None
    model = ExternalOpportunity if source_type == "nvoids" else RecruiterEmail
    row = db.query(model).filter(model.owner_id == owner_id, model.id == row_id).first()
    return row.record_id if row else None


def link_record_to_lineage(db: Session, *, record_id: str | None, lineage_id: str) -> None:
    """Purely additive - called right after an existing create_lineage/attach_recruiter_opportunity
    call to tell the independent Record layer about a lineage. Never blocks the real write."""
    if not record_id:
        return
    record = db.query(CandidateRecord).filter(CandidateRecord.id == record_id).first()
    if record is None:
        logger.warning("dangling_record_id record_id=%s lineage_id=%s", record_id, lineage_id)
        return
    record.internal_lineage_id = lineage_id


def get_record(db: Session, *, owner_id: str, record_id: str) -> CandidateRecord | None:
    return db.query(CandidateRecord).filter(
        CandidateRecord.owner_id == owner_id, CandidateRecord.id == record_id
    ).first()


def record_id_for_opportunity(db: Session, *, owner_id: str, recruiter_opportunity_id: int) -> str | None:
    row = db.query(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id == recruiter_opportunity_id
    ).first()
    return row.record_id if row else None


def record_id_for_application(db: Session, *, owner_id: str, application) -> str | None:
    """Resolve through the source email first, then the recruiter opportunity."""
    source_email_id = getattr(application, "source_recruiter_email_id", None)
    if source_email_id is not None:
        email = db.query(RecruiterEmail).filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.id == source_email_id,
        ).first()
        if email is not None and email.record_id:
            return email.record_id
    recruiter_opportunity_id = getattr(application, "recruiter_opportunity_id", None)
    if recruiter_opportunity_id is None:
        return None
    return record_id_for_opportunity(
        db,
        owner_id=owner_id,
        recruiter_opportunity_id=recruiter_opportunity_id,
    )


def application_link_ids_for_record(
    db: Session,
    *,
    owner_id: str,
    record_id: str,
) -> tuple[list[int], list[int]]:
    email_ids = [
        row_id
        for (row_id,) in db.query(RecruiterEmail.id).filter(
            RecruiterEmail.owner_id == owner_id,
            RecruiterEmail.record_id == record_id,
        )
    ]
    opportunity_ids = [
        row_id
        for (row_id,) in db.query(RecruiterOpportunity.id).filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.record_id == record_id,
        )
    ]
    return email_ids, opportunity_ids


def applications_for_record(db: Session, *, owner_id: str, record_id: str, model) -> list:
    email_ids, opportunity_ids = application_link_ids_for_record(
        db,
        owner_id=owner_id,
        record_id=record_id,
    )
    links = []
    if opportunity_ids:
        links.append(model.recruiter_opportunity_id.in_(opportunity_ids))
    if email_ids and hasattr(model, "source_recruiter_email_id"):
        links.append(model.source_recruiter_email_id.in_(email_ids))
    if not links:
        return []
    return (
        db.query(model)
        .filter(
            model.owner_id == owner_id,
            model.deleted_at.is_(None),
            or_(*links),
        )
        .order_by(model.created_at.asc(), model.id.asc())
        .all()
    )


def record_outcomes(db: Session, *, owner_id: str, record_id: str) -> dict[str, object]:
    emails = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.record_id == record_id)
        .all()
    )
    email_by_id = {row.id: row for row in emails}
    email_ids = list(email_by_id)
    open_count = (
        db.query(EmailOpenEvent)
        .filter(EmailOpenEvent.owner_id == owner_id, EmailOpenEvent.recruiter_email_id.in_(email_ids))
        .count()
        if email_ids
        else 0
    )
    conversations = (
        db.query(EmailConversation)
        .filter(
            EmailConversation.owner_id == owner_id,
            EmailConversation.root_recruiter_email_id.in_(email_ids),
        )
        .all()
        if email_ids
        else []
    )
    conversation_by_id = {row.id: row for row in conversations}
    inbound = (
        db.query(EmailReplyMessage)
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.conversation_id.in_(list(conversation_by_id)),
            EmailReplyMessage.direction == "inbound",
        )
        .order_by(EmailReplyMessage.received_at.asc(), EmailReplyMessage.id.asc())
        .all()
        if conversation_by_id
        else []
    )
    first_reply = inbound[0] if inbound else None
    days_to_first_reply = None
    if first_reply is not None:
        root_email = email_by_id.get(conversation_by_id[first_reply.conversation_id].root_recruiter_email_id)
        if root_email is not None and root_email.sent_at is not None:
            days_to_first_reply = max(
                0.0,
                (first_reply.received_at - root_email.sent_at).total_seconds() / 86400,
            )

    applications = applications_for_record(
        db,
        owner_id=owner_id,
        record_id=record_id,
        model=Application,
    )
    appts_applications = applications_for_record(
        db,
        owner_id=owner_id,
        record_id=record_id,
        model=AppTSApplication,
    )
    legacy_ids = [row.id for row in applications]
    appts_ids = [row.id for row in appts_applications]
    interviewed = bool(
        (
            legacy_ids
            and db.query(ApplicationInterview.id).filter(
                ApplicationInterview.owner_id == owner_id,
                ApplicationInterview.application_id.in_(legacy_ids),
                ApplicationInterview.deleted_at.is_(None),
            ).first()
        )
        or (
            appts_ids
            and db.query(AppTSApplicationInterview.id).filter(
                AppTSApplicationInterview.owner_id == owner_id,
                AppTSApplicationInterview.application_id.in_(appts_ids),
                AppTSApplicationInterview.deleted_at.is_(None),
            ).first()
        )
    )
    record = get_record(db, owner_id=owner_id, record_id=record_id)
    lineage = db.get(OpportunityLineage, record.internal_lineage_id) if record and record.internal_lineage_id else None
    source_state = emails[0].state if emails else None
    if source_state is None:
        external = db.query(ExternalOpportunity).filter(
            ExternalOpportunity.owner_id == owner_id,
            ExternalOpportunity.record_id == record_id,
        ).first()
        source_state = external.bridge_status if external else None
    return {
        "sent": any(row.sent_at is not None or row.sent_status == "sent" for row in emails),
        "sent_count": sum(1 for row in emails if row.sent_at is not None or row.sent_status == "sent"),
        "opened": open_count > 0 or any(row.open_count > 0 or row.opened_at is not None for row in emails),
        "open_count": max(open_count, sum(int(row.open_count or 0) for row in emails)),
        "replied": bool(inbound),
        "inbound_reply_count": len(inbound),
        "first_reply_at": first_reply.received_at if first_reply else None,
        "days_to_first_reply": days_to_first_reply,
        "interviewed": interviewed,
        "current_status": lineage.current_status if lineage is not None else source_state,
    }
