from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import (
    CandidateRecord,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    OpportunitySourceReference,
    RecruiterEmail,
    RecruiterOpportunity,
    utc_now,
)


logger = logging.getLogger(__name__)

SOURCE_TYPES = {"gmail", "nvoids"}


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
