from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    APPLICATION_STATUS_VALUES,
    Application,
    ApplicationEvent,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    utc_now,
)


APPLICATION_EVENT_TYPE_VALUES = (
    "created",
    "status_changed",
    "note",
    "email_linked",
    "call_note",
    "next_action_set",
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


class ApplicationReferenceNotFoundError(ValueError):
    pass


class ApplicationConflictError(ValueError):
    pass


class ApplicationValidationError(ValueError):
    pass


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
) -> Application:
    if new_status not in APPLICATION_STATUS_VALUES:
        raise ApplicationValidationError("Invalid application status")

    old_status = application.status
    now = utc_now()
    application.status = new_status
    application.status_changed_at = now
    if new_status == "resume_shared":
        application.resume_shared_at = now
    if new_status == "submitted_to_client":
        application.submitted_to_client_at = now
    application.closed_at = now if new_status in APPLICATION_CLOSED_STATUS_VALUES else None
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
