from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    APPLICATION_STATUS_VALUES,
    Application,
    ApplicationEvent,
    ApplicationInterview,
    ApplicationRTR,
    ApplicationSuggestion,
    AttachmentAsset,
    INTERVIEW_RESULT_VALUES,
    INTERVIEW_ROUND_TYPE_VALUES,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    utc_now,
)
from app.services import opportunity_lineage_service


logger = logging.getLogger(__name__)


APPLICATION_EVENT_TYPE_VALUES = (
    "created",
    "status_changed",
    "note",
    "email_linked",
    "call_note",
    "next_action_set",
    "duplicate_override",
    "recruiter_replied",
    "outreach_sent",
    "rtr_status_changed",
)
APPLICATION_EVENT_SOURCE_VALUES = ("user", "system")
WAITING_ON_RECRUITER_STATUS_VALUES = (
    "contacted",
    "resume_shared",
    "rtr_requested",
    "submitted_to_client",
    "client_reviewing",
)
INTERVIEW_STATUS_VALUES = ("interview_1", "interview_2", "final_interview")
CLOSED_REASON_CODE_VALUES = (
    "rate_mismatch",
    "skills_gap",
    "client_freeze",
    "position_filled",
    "candidate_declined",
    "recruiter_unresponsive",
    "other",
)
DUPLICATE_LOOKBACK_DAYS = 45


class ApplicationReferenceNotFoundError(ValueError):
    pass


class ApplicationConflictError(ValueError):
    pass


class ApplicationValidationError(ValueError):
    pass


class ApplicationDuplicateWarning(ValueError):
    def __init__(self, candidates: list[Application]):
        self.candidates = candidates
        super().__init__("Possible duplicate submission to the same end client")


def create_application(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
    recruiter_opportunity_id: int,
) -> Application:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.id == resume_asset_id)
        .first()
    )
    if not resume:
        raise ApplicationReferenceNotFoundError("Resume not found")

    opportunity = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.id == recruiter_opportunity_id,
        )
        .first()
    )
    if not opportunity:
        raise ApplicationReferenceNotFoundError("Opportunity not found")

    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.id == opportunity.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not recruiter:
        raise ApplicationReferenceNotFoundError("Recruiter contact not found")

    existing = (
        db.query(Application.id)
        .filter(
            Application.owner_id == owner_id,
            Application.resume_asset_id == resume_asset_id,
            Application.recruiter_opportunity_id == recruiter_opportunity_id,
        )
        .first()
    )
    if existing:
        raise ApplicationConflictError("This resume is already tracked for this opportunity")

    now = utc_now()
    application = Application(
        owner_id=owner_id,
        resume_asset_id=resume.id,
        resume_version_snapshot=resume.version,
        resume_file_name_snapshot=resume.file_name,
        resume_sha256_snapshot=resume.sha256,
        recruiter_opportunity_id=opportunity.id,
        recruiter_contact_id=recruiter.id,
        recruiter_name_snapshot=recruiter.recruiter_name or "",
        recruiter_company_snapshot=recruiter.company or "",
        job_title_snapshot=opportunity.job_title or "",
        end_client_snapshot=opportunity.end_client or "",
        status="matched",
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(application)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ApplicationConflictError("This resume is already tracked for this opportunity") from exc
    append_event(db, application, event_type="created", event_source="system")
    return application


def update_status(
    db: Session,
    application: Application,
    *,
    new_status: str,
    note: str | None = None,
    closed_reason_code: str | None = None,
) -> Application:
    if new_status not in APPLICATION_STATUS_VALUES:
        raise ApplicationValidationError("Invalid application status")
    if closed_reason_code is not None:
        if closed_reason_code not in CLOSED_REASON_CODE_VALUES:
            raise ApplicationValidationError("Invalid closed reason code")
        if new_status not in APPLICATION_CLOSED_STATUS_VALUES:
            raise ApplicationValidationError("Closed reason code requires a closed application status")

    old_status = application.status
    now = utc_now()
    application.status = new_status
    application.status_changed_at = now
    if new_status == "resume_shared":
        application.resume_shared_at = now
    if new_status == "submitted_to_client":
        application.submitted_to_client_at = now
    application.closed_at = now if new_status in APPLICATION_CLOSED_STATUS_VALUES else None
    if new_status not in APPLICATION_CLOSED_STATUS_VALUES:
        application.closed_reason_code = None
    elif closed_reason_code is not None:
        application.closed_reason_code = closed_reason_code
    append_event(
        db,
        application,
        event_type="status_changed",
        note=note or "",
        metadata={"from": old_status, "to": new_status},
    )
    return application


def append_event(
    db: Session,
    application: Application,
    *,
    event_type: str,
    note: str = "",
    event_source: str = "user",
    linked_recruiter_email_id: int | None = None,
    metadata: dict[str, object] | None = None,
) -> ApplicationEvent:
    if event_type not in APPLICATION_EVENT_TYPE_VALUES:
        raise ApplicationValidationError("Invalid application event type")
    if event_source not in APPLICATION_EVENT_SOURCE_VALUES:
        raise ApplicationValidationError("Invalid application event source")
    if event_type == "email_linked" and linked_recruiter_email_id is None:
        raise ApplicationValidationError("linked_recruiter_email_id is required for email_linked events")
    if linked_recruiter_email_id is not None:
        linked_email = (
            db.query(RecruiterEmail.id)
            .filter(
                RecruiterEmail.owner_id == application.owner_id,
                RecruiterEmail.id == linked_recruiter_email_id,
            )
            .first()
        )
        if not linked_email:
            raise ApplicationReferenceNotFoundError("Recruiter email not found")

    now = utc_now()
    event = ApplicationEvent(
        owner_id=application.owner_id,
        application_id=application.id,
        event_type=event_type,
        event_source=event_source,
        note=note,
        linked_recruiter_email_id=linked_recruiter_email_id,
        metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        occurred_at=now,
        created_at=now,
    )
    db.add(event)
    lineage = opportunity_lineage_service.get_lineage_for_opportunity(
        db,
        owner_id=application.owner_id,
        recruiter_opportunity_id=application.recruiter_opportunity_id,
    )
    if lineage is None:
        logger.warning(
            "Missing opportunity lineage for application %s and recruiter opportunity %s",
            application.id,
            application.recruiter_opportunity_id,
        )
    else:
        lineage_metadata = dict(metadata or {})
        if linked_recruiter_email_id is not None:
            lineage_metadata.setdefault("linked_recruiter_email_id", linked_recruiter_email_id)
        opportunity_lineage_service.record_event(
            db,
            lineage_id=lineage.id,
            event_type=event_type,
            actor=event_source,
            process_name="application_service",
            related_record_type="Application",
            related_record_id=application.id,
            note=note,
            metadata=lineage_metadata,
        )
    if event_type in {"email_linked", "call_note"}:
        application.last_contact_at = now
    return event


def set_next_action(
    db: Session,
    application: Application,
    *,
    next_action_type: str | None,
    next_action_at: datetime | None,
) -> Application:
    application.next_action_type = next_action_type
    application.next_action_at = next_action_at
    append_event(
        db,
        application,
        event_type="next_action_set",
        metadata={
            "next_action_type": next_action_type,
            "next_action_at": next_action_at.isoformat() if next_action_at else None,
        },
    )
    return application


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def find_duplicate_candidates(
    db: Session,
    *,
    owner_id: str,
    end_client: str,
    job_title: str,
    exclude_application_id: int,
) -> list[Application]:
    normalized_client = _normalize_text(end_client)
    normalized_title = _normalize_text(job_title)
    if not normalized_client or not normalized_title:
        return []
    rows = (
        db.query(Application)
        .filter(
            Application.owner_id == owner_id,
            Application.deleted_at.is_(None),
            Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
            Application.id != exclude_application_id,
            Application.created_at >= utc_now() - timedelta(days=DUPLICATE_LOOKBACK_DAYS),
        )
        .order_by(Application.created_at.desc(), Application.id.desc())
        .all()
    )
    return [
        row
        for row in rows
        if _normalize_text(row.end_client_snapshot) == normalized_client
        and (candidate_title := _normalize_text(row.job_title_snapshot))
        and (
            candidate_title == normalized_title
            or normalized_title in candidate_title
            or candidate_title in normalized_title
        )
    ]


def request_rtr(
    db: Session,
    application: Application,
    *,
    role_scope: str,
    end_client_scope: str,
    expires_at: datetime | None = None,
) -> ApplicationRTR:
    now = utc_now()
    rtr = ApplicationRTR(
        owner_id=application.owner_id,
        application_id=application.id,
        status="requested",
        role_scope=role_scope,
        end_client_scope=end_client_scope,
        requested_at=now,
        expires_at=expires_at,
        created_at=now,
        updated_at=now,
    )
    db.add(rtr)
    update_status(db, application, new_status="rtr_requested")
    return rtr


def confirm_rtr(
    db: Session,
    application: Application,
    rtr: ApplicationRTR,
    *,
    proof_attachment_id: int | None = None,
    proof_recruiter_email_id: int | None = None,
) -> ApplicationRTR:
    if rtr.owner_id != application.owner_id or rtr.application_id != application.id:
        raise ApplicationReferenceNotFoundError("RTR not found")
    if proof_attachment_id is None and proof_recruiter_email_id is None:
        raise ApplicationValidationError("RTR confirmation requires attachment or recruiter email proof")
    if proof_attachment_id is not None:
        attachment = (
            db.query(AttachmentAsset.id)
            .filter(
                AttachmentAsset.owner_id == application.owner_id,
                AttachmentAsset.id == proof_attachment_id,
            )
            .first()
        )
        if not attachment:
            raise ApplicationReferenceNotFoundError("Attachment not found")
    if proof_recruiter_email_id is not None:
        recruiter_email = (
            db.query(RecruiterEmail.id)
            .filter(
                RecruiterEmail.owner_id == application.owner_id,
                RecruiterEmail.id == proof_recruiter_email_id,
            )
            .first()
        )
        if not recruiter_email:
            raise ApplicationReferenceNotFoundError("Recruiter email not found")
    now = utc_now()
    rtr.status = "confirmed"
    rtr.confirmed_at = now
    rtr.proof_attachment_id = proof_attachment_id
    rtr.proof_recruiter_email_id = proof_recruiter_email_id
    rtr.updated_at = now
    update_status(db, application, new_status="rtr_confirmed")
    return rtr


def expire_or_revoke_rtr(
    db: Session,
    application: Application,
    rtr: ApplicationRTR,
    *,
    new_status: str,
) -> ApplicationRTR:
    if new_status not in {"expired", "revoked"}:
        raise ApplicationValidationError("RTR status must be expired or revoked")
    rtr.status = new_status
    rtr.updated_at = utc_now()
    append_event(
        db,
        application,
        event_type="rtr_status_changed",
        metadata={"rtr_id": rtr.id, "to": new_status},
    )
    return rtr


def submit_to_client(
    db: Session,
    application: Application,
    *,
    override_duplicate_warning: bool = False,
) -> tuple[Application, list[Application]]:
    candidates = find_duplicate_candidates(
        db,
        owner_id=application.owner_id,
        end_client=application.end_client_snapshot,
        job_title=application.job_title_snapshot,
        exclude_application_id=application.id,
    )
    if candidates and not override_duplicate_warning:
        raise ApplicationDuplicateWarning(candidates)
    update_status(db, application, new_status="submitted_to_client")
    if candidates:
        append_event(
            db,
            application,
            event_type="duplicate_override",
            metadata={"acknowledged_duplicate_ids": [candidate.id for candidate in candidates]},
        )
    return application, candidates


def add_interview(
    db: Session,
    application: Application,
    *,
    round_type: str,
    scheduled_at: datetime | None = None,
    format: str = "",
    interviewer_names: str = "",
    sync_application_status: bool = True,
) -> ApplicationInterview:
    if round_type not in INTERVIEW_ROUND_TYPE_VALUES:
        raise ApplicationValidationError("Invalid interview round type")
    now = utc_now()
    interview = ApplicationInterview(
        owner_id=application.owner_id,
        application_id=application.id,
        round_type=round_type,
        scheduled_at=scheduled_at,
        format=format,
        interviewer_names=interviewer_names,
        created_at=now,
        updated_at=now,
    )
    db.add(interview)
    if sync_application_status and round_type in INTERVIEW_STATUS_VALUES:
        update_status(db, application, new_status=round_type)
    return interview


def update_interview(
    db: Session,
    interview: ApplicationInterview,
    *,
    scheduled_at: datetime | None = None,
    format: str | None = None,
    interviewer_names: str | None = None,
    feedback: str | None = None,
    result: str | None = None,
    follow_up_task_note: str | None = None,
) -> ApplicationInterview:
    if result is not None and result not in INTERVIEW_RESULT_VALUES:
        raise ApplicationValidationError("Invalid interview result")
    if scheduled_at is not None:
        interview.scheduled_at = scheduled_at
    for field, value in (
        ("format", format),
        ("interviewer_names", interviewer_names),
        ("feedback", feedback),
        ("result", result),
        ("follow_up_task_note", follow_up_task_note),
    ):
        if value is not None:
            setattr(interview, field, value)
    interview.updated_at = utc_now()
    return interview


def delete_interview(db: Session, interview: ApplicationInterview) -> None:
    interview.deleted_at = utc_now()


def accept_suggestion(
    db: Session,
    suggestion: ApplicationSuggestion,
    *,
    application: Application,
    override_next_action_at: datetime | None = None,
) -> Application:
    if suggestion.owner_id != application.owner_id or suggestion.application_id != application.id:
        raise ApplicationReferenceNotFoundError("Suggestion not found")
    if suggestion.status != "pending":
        raise ApplicationValidationError("Suggestion is already resolved")
    if suggestion.suggestion_type == "link_reply":
        append_event(
            db,
            application,
            event_type="email_linked",
            event_source="system",
            linked_recruiter_email_id=suggestion.recruiter_email_id,
            metadata={"reply_message_id": suggestion.reply_message_id},
        )
    elif suggestion.suggestion_type == "status_change":
        if not suggestion.suggested_status:
            raise ApplicationValidationError("Suggestion has no status")
        update_status(
            db,
            application,
            new_status=suggestion.suggested_status,
            note=f"Accepted suggestion: {suggestion.reason}",
        )
    elif suggestion.suggestion_type == "next_action":
        if not suggestion.suggested_next_action_type:
            raise ApplicationValidationError("Suggestion has no next action")
        set_next_action(
            db,
            application,
            next_action_type=suggestion.suggested_next_action_type,
            next_action_at=override_next_action_at or suggestion.suggested_next_action_at,
        )
    elif suggestion.suggestion_type == "stale_prompt":
        append_event(
            db,
            application,
            event_type="note",
            event_source="system",
            note=suggestion.reason,
        )
    else:
        raise ApplicationValidationError("Invalid application suggestion type")
    suggestion.status = "accepted"
    suggestion.resolved_at = utc_now()
    return application


def dismiss_suggestion(db: Session, suggestion: ApplicationSuggestion) -> None:
    _ = db
    if suggestion.status != "pending":
        raise ApplicationValidationError("Suggestion is already resolved")
    suggestion.status = "dismissed"
    suggestion.resolved_at = utc_now()


def dashboard_summary(db: Session, owner_id: str) -> dict[str, int]:
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    active = (
        Application.owner_id == owner_id,
        Application.deleted_at.is_(None),
    )

    def count(*filters: object) -> int:
        return int(db.query(func.count(Application.id)).filter(*active, *filters).scalar() or 0)

    return {
        "due_today": count(
            Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
            Application.next_action_at >= today_start,
            Application.next_action_at < today_end,
        ),
        "waiting_on_recruiter": count(Application.status.in_(WAITING_ON_RECRUITER_STATUS_VALUES)),
        "interviews": count(Application.status.in_(INTERVIEW_STATUS_VALUES)),
        "closed_recent": count(
            Application.status.in_(APPLICATION_CLOSED_STATUS_VALUES),
            Application.closed_at >= now - timedelta(days=14),
        ),
    }
