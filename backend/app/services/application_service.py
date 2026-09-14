from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
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
    ApplicationOutreachMessage,
    ApplicationRTR,
    ApplicationSuggestion,
    ApplicationSkillGapSnapshot,
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


@dataclass(frozen=True)
class ApplicationModels:
    application_cls: type
    event_cls: type
    rtr_cls: type
    interview_cls: type
    suggestion_cls: type
    skill_gap_snapshot_cls: type
    outreach_message_cls: type
    related_record_type: str


LEGACY_MODELS = ApplicationModels(
    Application,
    ApplicationEvent,
    ApplicationRTR,
    ApplicationInterview,
    ApplicationSuggestion,
    ApplicationSkillGapSnapshot,
    ApplicationOutreachMessage,
    "Application",
)


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
    'resume_submission_status_changed',
    'manual_submission_logged',
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
    dedupe_key: str,
) -> tuple[Application, bool]:
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

    dedupe_key = dedupe_key.strip()
    if not dedupe_key or len(dedupe_key) > 64:
        raise ApplicationValidationError('dedupe_key is required and must be at most 64 characters')

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
        dedupe_key=dedupe_key,
        status="matched",
        status_changed_at=now,
        created_at=now,
        updated_at=now,
    )
    try:
        with db.begin_nested():
            db.add(application)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(Application)
            .filter(Application.owner_id == owner_id, Application.dedupe_key == dedupe_key)
            .first()
        )
        if existing is None:
            raise
        return existing, False
    append_event(db, application, event_type="created", event_source="system")
    return application, True


def _json_list(raw: str | None) -> list[object]:
    try:
        value = json.loads(raw or '[]')
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _json_dict(raw: str | None) -> dict[str, object]:
    try:
        value = json.loads(raw or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _record_milestone(application: Application, name: str, occurred_at: datetime) -> None:
    milestones = _json_dict(application.milestones_reached_json)
    milestones.setdefault(name, occurred_at.isoformat())
    application.milestones_reached_json = json.dumps(milestones, separators=(',', ':'))


def _set_resume_submission_status(
    db: Session,
    application: Application,
    *,
    new_status: str,
    trigger: str,
    models: ApplicationModels = LEGACY_MODELS,
) -> bool:
    old_status = application.resume_submission_status
    if old_status == new_status:
        return False
    application.resume_submission_status = new_status
    append_event(
        db,
        application,
        event_type='resume_submission_status_changed',
        event_source='system',
        metadata={'from': old_status, 'to': new_status, 'trigger': trigger},
        models=models,
    )
    return True


def _mark_submitted(
    db: Session,
    application: Application,
    *,
    trigger: str,
    models: ApplicationModels = LEGACY_MODELS,
) -> None:
    if application.resume_submission_status != 'not_submitted':
        return
    resume = (
        db.query(ResumeAsset)
        .filter(
            ResumeAsset.owner_id == application.owner_id,
            ResumeAsset.id == application.resume_asset_id,
        )
        .first()
    )
    if resume is None:
        raise ApplicationReferenceNotFoundError('Resume for this application no longer exists')
    from app.services import resume_tracking_service

    application.resume_submitted_at = application.resume_submitted_at or utc_now()
    application.resume_skills_snapshot_json = json.dumps(
        resume_tracking_service.snapshot_resume_skills(resume),
        separators=(',', ':'),
    )
    application.resume_primary_role_snapshot = (resume.primary_role or '').strip()
    _set_resume_submission_status(
        db,
        application,
        new_status='submitted',
        trigger=trigger,
        models=models,
    )
    resume_tracking_service.compute_skill_gap(db, application, models=models)


def mark_resume_submitted_if_needed(
    db: Session,
    application: Application,
    *,
    models: ApplicationModels = LEGACY_MODELS,
) -> None:
    _mark_submitted(db, application, trigger='outreach_send', models=models)


def _append_ai_missing_skill_tags(
    db: Session,
    application: Application,
    *,
    models: ApplicationModels = LEGACY_MODELS,
) -> None:
    snapshot_cls = models.skill_gap_snapshot_cls
    snapshot = (
        db.query(snapshot_cls)
        .filter(
            snapshot_cls.owner_id == application.owner_id,
            snapshot_cls.application_id == application.id,
        )
        .first()
    )
    if snapshot is None:
        return
    tags = [tag for tag in _json_list(application.rejection_detail_tags_json) if isinstance(tag, dict)]
    existing = {
        (str(tag.get('category') or ''), str(tag.get('value') or '').strip().casefold())
        for tag in tags
    }
    for value in _json_list(snapshot.missing_required_json):
        skill = str(value or '').strip()
        key = ('missing_skill', skill.casefold())
        if not skill or key in existing:
            continue
        tags.append(
            {
                'category': 'missing_skill',
                'value': skill,
                'source': 'ai',
                'confirmed_at': None,
            }
        )
        existing.add(key)
    application.rejection_detail_tags_json = json.dumps(tags, separators=(',', ':'))


def update_status(
    db: Session,
    application: Application,
    *,
    new_status: str,
    note: str | None = None,
    closed_reason_code: str | None = None,
    models: ApplicationModels = LEGACY_MODELS,
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
        models=models,
    )
    if models.related_record_type == "AppTSApplication":
        from app.services import appts_service, label_tracking_service

        db.flush()
        if new_status in label_tracking_service.APPLICATION_WATCH_TERMINAL_STATUSES:
            label_tracking_service.reconcile_watches(db, application.owner_id)
        elif old_status in label_tracking_service.APPLICATION_WATCH_TERMINAL_STATUSES:
            appts_service.derive_application_watches(db, application.owner_id, application)
    should_derive = new_status in APPLICATION_CLOSED_STATUS_VALUES
    if (
        not should_derive
        and old_status not in APPLICATION_CLOSED_STATUS_VALUES
        and new_status not in APPLICATION_CLOSED_STATUS_VALUES
    ):
        should_derive = APPLICATION_STATUS_VALUES.index(new_status) > APPLICATION_STATUS_VALUES.index(old_status)
    if not should_derive:
        return application

    desired_status: str | None = None
    milestone: str | None = None
    if new_status not in APPLICATION_CLOSED_STATUS_VALUES:
        if APPLICATION_STATUS_VALUES.index(new_status) >= APPLICATION_STATUS_VALUES.index('resume_shared'):
            _mark_submitted(db, application, trigger='status_derivation', models=models)
        if new_status in INTERVIEW_STATUS_VALUES:
            desired_status = 'interview_scheduled'
            milestone = 'interview_scheduled'
        elif new_status == 'offer':
            desired_status = 'offered'
            milestone = 'offered'
    elif new_status == 'hired':
        desired_status = 'hired'
        milestone = 'hired'
    elif new_status == 'rejected':
        desired_status = 'rejected'
    elif new_status == 'withdrawn':
        desired_status = 'withdrawn'

    if desired_status is not None:
        _set_resume_submission_status(
            db,
            application,
            new_status=desired_status,
            trigger='status_derivation',
            models=models,
        )
    if milestone is not None:
        _record_milestone(application, milestone, now)
    if new_status == 'rejected':
        _append_ai_missing_skill_tags(db, application, models=models)
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
    models: ApplicationModels = LEGACY_MODELS,
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
    event = models.event_cls(
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
    lineage = (
        opportunity_lineage_service.get_lineage_for_opportunity(
            db,
            owner_id=application.owner_id,
            recruiter_opportunity_id=application.recruiter_opportunity_id,
        )
        if application.recruiter_opportunity_id is not None
        else None
    )
    if lineage is None:
        if application.recruiter_opportunity_id is not None:
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
            related_record_type=models.related_record_type,
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
    models: ApplicationModels = LEGACY_MODELS,
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
        models=models,
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
    models: ApplicationModels = LEGACY_MODELS,
) -> list[Application]:
    normalized_client = _normalize_text(end_client)
    normalized_title = _normalize_text(job_title)
    if not normalized_client or not normalized_title:
        return []
    rows = (
        db.query(models.application_cls)
        .filter(
            models.application_cls.owner_id == owner_id,
            models.application_cls.deleted_at.is_(None),
            models.application_cls.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
            models.application_cls.id != exclude_application_id,
            models.application_cls.created_at >= utc_now() - timedelta(days=DUPLICATE_LOOKBACK_DAYS),
        )
        .order_by(models.application_cls.created_at.desc(), models.application_cls.id.desc())
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
    models: ApplicationModels = LEGACY_MODELS,
) -> ApplicationRTR:
    now = utc_now()
    rtr = models.rtr_cls(
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
    update_status(db, application, new_status="rtr_requested", models=models)
    return rtr


def confirm_rtr(
    db: Session,
    application: Application,
    rtr: ApplicationRTR,
    *,
    proof_attachment_id: int | None = None,
    proof_recruiter_email_id: int | None = None,
    models: ApplicationModels = LEGACY_MODELS,
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
    update_status(db, application, new_status="rtr_confirmed", models=models)
    return rtr


def expire_or_revoke_rtr(
    db: Session,
    application: Application,
    rtr: ApplicationRTR,
    *,
    new_status: str,
    models: ApplicationModels = LEGACY_MODELS,
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
        models=models,
    )
    return rtr


def submit_to_client(
    db: Session,
    application: Application,
    *,
    override_duplicate_warning: bool = False,
    models: ApplicationModels = LEGACY_MODELS,
) -> tuple[Application, list[Application]]:
    candidates = find_duplicate_candidates(
        db,
        owner_id=application.owner_id,
        end_client=application.end_client_snapshot,
        job_title=application.job_title_snapshot,
        exclude_application_id=application.id,
        models=models,
    )
    if candidates and not override_duplicate_warning:
        raise ApplicationDuplicateWarning(candidates)
    update_status(db, application, new_status="submitted_to_client", models=models)
    if candidates:
        append_event(
            db,
            application,
            event_type="duplicate_override",
            metadata={"acknowledged_duplicate_ids": [candidate.id for candidate in candidates]},
            models=models,
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
    models: ApplicationModels = LEGACY_MODELS,
) -> ApplicationInterview:
    if round_type not in INTERVIEW_ROUND_TYPE_VALUES:
        raise ApplicationValidationError("Invalid interview round type")
    now = utc_now()
    interview = models.interview_cls(
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
        update_status(db, application, new_status=round_type, models=models)
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
    models: ApplicationModels = LEGACY_MODELS,
) -> ApplicationInterview:
    _ = models
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


def delete_interview(
    db: Session,
    interview: ApplicationInterview,
    *,
    models: ApplicationModels = LEGACY_MODELS,
) -> None:
    _ = (db, models)
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
    elif suggestion.suggestion_type in {
        'new_variant_needed',
        'email_positioning',
        'skill_gap_pattern',
    }:
        append_event(
            db,
            application,
            event_type='note',
            event_source='system',
            note=suggestion.reason,
            metadata={
                'suggestion_type': suggestion.suggestion_type,
                'payload': _json_dict(suggestion.payload_json),
            },
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
