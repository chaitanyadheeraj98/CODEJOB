from __future__ import annotations

import json
import hashlib
import logging
from datetime import datetime

from rq import Retry
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.jobs.queues import EMBEDDING_QUEUE, get_queue
from app.jobs.tasks import run_generate_embedding_job
from app.models import (
    AppTSApplication,
    AppTSApplicationEvent,
    AppTSApplicationInterview,
    AppTSApplicationOutreachMessage,
    AppTSApplicationRTR,
    AppTSApplicationSkillGapSnapshot,
    AppTSApplicationSuggestion,
    Application,
    PremiumContactEmail,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    TrackedThread,
    EmailConversation,
    RecruiterWatch,
    UserSettings,
    utc_now,
)
from app.services import application_service, end_client_validation, label_tracking_service, recruiter_identity_service, resume_tracking_service

logger = logging.getLogger(__name__)

APPTS_MODELS = application_service.ApplicationModels(
    AppTSApplication,
    AppTSApplicationEvent,
    AppTSApplicationRTR,
    AppTSApplicationInterview,
    AppTSApplicationSuggestion,
    AppTSApplicationSkillGapSnapshot,
    AppTSApplicationOutreachMessage,
    "AppTSApplication",
)


def enqueue_embedding_generation(record_id: int) -> None:
    try:
        get_queue(EMBEDDING_QUEUE).enqueue(
            run_generate_embedding_job,
            kwargs={"record_type": "appts_application", "record_id": record_id, "owner_id": tenancy.owner_id()},
            retry=Retry(max=3, interval=[10, 30, 90]),
            job_timeout=60,
            result_ttl=3600,
            failure_ttl=86400,
        )
    except Exception:
        logger.warning("embedding_enqueue_failed record_type=appts_application record_id=%s", record_id, exc_info=True)


def _resolved_identity(db: Session, owner_id: str, email: str) -> tuple[int | None, str | None]:
    normalized = email.strip().lower()
    if not normalized:
        return None, None
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.id.in_(
                db.query(PremiumContactEmail.premium_contact_id).filter(
                    PremiumContactEmail.owner_id == owner_id,
                    PremiumContactEmail.normalized_email == normalized,
                )
            ),
        )
        .first()
    )
    return (contact.id if contact else None), normalized


def _source_email_id_for_record(db: Session, *, owner_id: str, record_id: str | None) -> int | None:
    if not record_id:
        return None
    row = (
        db.query(RecruiterEmail.id)
        .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.record_id == record_id)
        .order_by(
            RecruiterEmail.sent_at.is_(None),
            RecruiterEmail.sent_at.desc(),
            RecruiterEmail.id.desc(),
        )
        .first()
    )
    return row[0] if row else None


def derive_application_watches(db: Session, owner_id: str, application: AppTSApplication) -> list[RecruiterWatch]:
    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    if (
        application.owner_id != owner_id
        or application.deleted_at is not None
        or application.status in label_tracking_service.APPLICATION_WATCH_TERMINAL_STATUSES
        or user_settings is None
        or not user_settings.feature_application_watches_enabled
    ):
        return []
    address = label_tracking_service.normalize_address(
        application.resolved_recruiter_email or application.manual_recruiter_email
    )
    if not label_tracking_service._ADDRESS.fullmatch(address):
        return []
    domain = label_tracking_service.domain_of(address)
    active_count = [db.query(RecruiterWatch).filter(
        RecruiterWatch.owner_id == owner_id,
        RecruiterWatch.released_at.is_(None),
    ).count()]
    watches = []
    for kind, value in [("address", address), ("domain", domain)]:
        watch = label_tracking_service._upsert_watch(
            db,
            owner_id,
            kind=kind,
            value=value,
            application_id=application.id,
            active_count=active_count,
        )
        if watch is not None:
            watches.append(watch)
    return watches


def backfill_application_watches(db: Session, owner_id: str) -> int:
    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    if user_settings is None or not user_settings.feature_application_watches_enabled:
        return 0
    for application in db.query(AppTSApplication).filter(
        AppTSApplication.owner_id == owner_id,
        AppTSApplication.deleted_at.is_(None),
        AppTSApplication.status.not_in(label_tracking_service.APPLICATION_WATCH_TERMINAL_STATUSES),
    ).order_by(AppTSApplication.id):
        derive_application_watches(db, owner_id, application)
    return db.query(RecruiterWatch).filter(
        RecruiterWatch.owner_id == owner_id,
        RecruiterWatch.released_at.is_(None),
    ).count()


def _insert(db: Session, application: AppTSApplication) -> tuple[AppTSApplication, bool]:
    try:
        with db.begin_nested():
            db.add(application)
            db.flush()
    except IntegrityError:
        existing = (
            db.query(AppTSApplication)
            .filter(
                AppTSApplication.owner_id == application.owner_id,
                AppTSApplication.dedupe_key == application.dedupe_key,
            )
            .first()
        )
        if existing is None:
            raise
        return existing, False
    application_service.append_event(
        db,
        application,
        event_type="created",
        event_source="user",
        models=APPTS_MODELS,
    )
    derive_application_watches(db, application.owner_id, application)
    return application, True


def create_tracked_application_manual(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
    dedupe_key: str,
    manual_recruiter_name: str,
    manual_recruiter_company: str,
    manual_job_title: str,
    manual_end_client: str = "",
    manual_recruiter_email: str = "",
    manual_recruiter_phone: str = "",
    manual_recruiter_linkedin_url: str = "",
    manual_jd_text: str = "",
    manual_source_note: str = "",
    submission_method: str = "email",
    resume_submitted_at: datetime | None = None,
    location_snapshot: str = "",
    source_recruiter_email_id: int | None = None,
    recruiter_opportunity_id: int | None = None,
    recruiter_contact_id: int | None = None,
) -> tuple[AppTSApplication, bool]:
    resume = db.query(ResumeAsset).filter(ResumeAsset.owner_id == owner_id, ResumeAsset.id == resume_asset_id).first()
    if resume is None:
        raise application_service.ApplicationReferenceNotFoundError("Resume not found")
    dedupe_key = dedupe_key.strip()
    if not dedupe_key or len(dedupe_key) > 64:
        raise application_service.ApplicationValidationError("dedupe_key is required and must be at most 64 characters")
    required = (manual_recruiter_name, manual_recruiter_company, manual_job_title)
    if any(not value.strip() for value in required):
        raise application_service.ApplicationValidationError("Recruiter, company, and job title must not be blank")
    # End client is optional on purpose. It is stated in a minority of postings,
    # and the three call paths below used to substitute the recruiter or posting
    # company when it was absent - which is why every tracked application
    # carried a staffing firm or the literal "Unknown" in an end-client column.
    # Blank here means *not identified*, never "this job has no end client".
    manual_end_client = end_client_validation.clean_end_client(manual_end_client)
    opportunity = None
    if recruiter_opportunity_id is not None:
        opportunity = (
            db.query(RecruiterOpportunity)
            .filter(
                RecruiterOpportunity.owner_id == owner_id,
                RecruiterOpportunity.id == recruiter_opportunity_id,
            )
            .first()
        )
        if opportunity is None:
            raise application_service.ApplicationReferenceNotFoundError("Opportunity not found")
        recruiter_contact_id = recruiter_contact_id or opportunity.recruiter_number_id
    if recruiter_contact_id is not None:
        contact_exists = (
            db.query(PremiumNumberContact.id)
            .filter(
                PremiumNumberContact.owner_id == owner_id,
                PremiumNumberContact.id == recruiter_contact_id,
                PremiumNumberContact.deleted_at.is_(None),
            )
            .first()
        )
        if contact_exists is None:
            raise application_service.ApplicationReferenceNotFoundError("Recruiter contact not found")
    if source_recruiter_email_id is not None:
        email_exists = (
            db.query(RecruiterEmail.id)
            .filter(
                RecruiterEmail.owner_id == owner_id,
                RecruiterEmail.id == source_recruiter_email_id,
            )
            .first()
        )
        if email_exists is None:
            raise application_service.ApplicationReferenceNotFoundError("Recruiter email not found")
    submitted_at = resume_submitted_at or utc_now()
    contact_id, resolved_email = _resolved_identity(db, owner_id, manual_recruiter_email)
    resolved_contact_id = contact_id or recruiter_contact_id
    row = AppTSApplication(
        owner_id=owner_id, resume_asset_id=resume.id, resume_version_snapshot=resume.version,
        resume_file_name_snapshot=resume.file_name, resume_sha256_snapshot=resume.sha256,
        recruiter_opportunity_id=recruiter_opportunity_id, recruiter_contact_id=recruiter_contact_id,
        recruiter_name_snapshot=manual_recruiter_name.strip(), recruiter_company_snapshot=manual_recruiter_company.strip(),
        job_title_snapshot=manual_job_title.strip(), end_client_snapshot=manual_end_client.strip(), location_snapshot=location_snapshot.strip(),
        manual_recruiter_name=manual_recruiter_name.strip(), manual_recruiter_company=manual_recruiter_company.strip(),
        manual_recruiter_email=manual_recruiter_email.strip(), manual_recruiter_phone=manual_recruiter_phone.strip(),
        manual_recruiter_linkedin_url=manual_recruiter_linkedin_url.strip(), manual_job_title=manual_job_title.strip(),
        manual_end_client=manual_end_client.strip(), manual_jd_text=manual_jd_text.strip(), manual_source_note=manual_source_note.strip(),
        status="resume_shared", status_changed_at=submitted_at, resume_shared_at=submitted_at,
        resume_submission_status="submitted", resume_submitted_at=submitted_at, submission_method=submission_method.strip(),
        dedupe_key=dedupe_key, resume_skills_snapshot_json=json.dumps(resume_tracking_service.snapshot_resume_skills(resume), separators=(",", ":")),
        resume_primary_role_snapshot=(resume.primary_role or "").strip(), resolved_recruiter_contact_id=resolved_contact_id,
        resolved_recruiter_email=resolved_email, source_recruiter_email_id=source_recruiter_email_id,
        created_at=utc_now(), updated_at=utc_now(),
    )
    result = _insert(db, row)
    if result[1]:
        resume_tracking_service.compute_skill_gap(db, row, models=APPTS_MODELS)
    return result


def create_tracked_application_from_label_thread(db: Session, *, owner_id: str, thread: TrackedThread, resume_asset_id: int) -> tuple[AppTSApplication, bool]:
    if thread.owner_id != owner_id:
        raise application_service.ApplicationReferenceNotFoundError("Thread not found")
    dedupe_key = hashlib.sha256(f"label:{owner_id}:{thread.external_thread_id}".encode()).hexdigest()
    existing = db.query(AppTSApplication).filter(AppTSApplication.owner_id == owner_id, AppTSApplication.dedupe_key == dedupe_key).first()
    if existing is not None:
        return existing, False
    if thread.untracked_at is not None:
        raise application_service.ApplicationValidationError("Thread is no longer label tracked")
    conversation = db.query(EmailConversation).filter(EmailConversation.owner_id == owner_id, EmailConversation.id == thread.conversation_id).first()
    if conversation is None:
        raise application_service.ApplicationReferenceNotFoundError("Conversation not found")
    from app.services.email_inbox_service import conversation_detail
    detail = conversation_detail(db, owner_id, conversation.id)
    row, created = create_tracked_application_manual(db, owner_id=owner_id, resume_asset_id=resume_asset_id,
        dedupe_key=dedupe_key, manual_recruiter_name=detail.recruiter or "Unknown",
        manual_recruiter_company="Unknown", manual_recruiter_email=detail.recruiter_email or "",
        manual_job_title=thread.subject_snapshot or "Not specified",
        manual_source_note=f"Tracked from Gmail thread {thread.external_thread_id}",
        source_recruiter_email_id=conversation.root_recruiter_email_id)
    if created:
        row.tracking_origin = "label"
        row.source_thread_id = thread.external_thread_id
        row.source_label_external_id = next(iter(json.loads(thread.label_external_ids_json)), None)
        row.status = "matched"
        row.resume_submission_status = "not_submitted"
        row.resume_submitted_at = row.resume_shared_at = None
        db.flush()
    return row, created


def create_tracked_application_from_record(db: Session, *, owner_id: str, recruiter_email_id: int, resume_asset_id: int, dedupe_key: str) -> tuple[AppTSApplication, bool]:
    email = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.id == recruiter_email_id).first()
    if email is None:
        raise application_service.ApplicationReferenceNotFoundError("Recruiter email not found")
    recruiter = recruiter_identity_service.recruiter_identity_for(db, email, owner_id=owner_id)
    row, created = create_tracked_application_manual(db, owner_id=owner_id, resume_asset_id=resume_asset_id,
        dedupe_key=dedupe_key, manual_recruiter_name=recruiter.name or "Unknown",
        manual_recruiter_company=recruiter.company or "Unknown", manual_recruiter_email=recruiter.address,
        manual_job_title=email.role or email.subject or "Not specified", manual_end_client=email.end_client or "",
        location_snapshot=email.location or "", source_recruiter_email_id=email.id)
    if created:
        row.tracking_origin = "email"
        row.status = "matched"
        row.resume_submission_status = "not_submitted"
        row.resume_submitted_at = row.resume_shared_at = None
        db.flush()
    return row, created


def create_tracked_application_from_email(
    db: Session,
    email: RecruiterEmail,
    *,
    owner_id: str,
) -> tuple[AppTSApplication, bool] | None:
    if not email.resume_asset_id:
        return None
    # Same correction as resume_tracking_service: the sender of a forwarded
    # requirement is not the recruiter it was sent to, and `email.company` is the
    # sender's firm by the extractor's own definition.
    recruiter = recruiter_identity_service.recruiter_identity_for(db, email, owner_id=owner_id)
    return create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=email.resume_asset_id, dedupe_key=f"appts_email:{email.id}",
        manual_recruiter_name=recruiter.name or "Unknown", manual_recruiter_company=recruiter.company or "Unknown",
        manual_recruiter_email=recruiter.address, manual_job_title=(email.role or "").strip() or "Not specified",
        manual_end_client=(email.end_client or "").strip(), manual_source_note=f"Tracked from email {email.id}",
        resume_submitted_at=email.sent_at, location_snapshot=email.location or "",
        source_recruiter_email_id=email.id,
    )


def create_tracked_application_from_opportunity(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int | None = None,
    recruiter_opportunity_id: int,
    dedupe_key: str,
) -> tuple[AppTSApplication, bool]:
    opportunity = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id == recruiter_opportunity_id).first()
    if opportunity is None:
        raise application_service.ApplicationReferenceNotFoundError("Opportunity not found")
    recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id == opportunity.recruiter_number_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if recruiter is None:
        raise application_service.ApplicationReferenceNotFoundError("Recruiter contact not found")
    effective_resume_id = resume_asset_id or opportunity.resume_asset_id
    if effective_resume_id is None:
        raise application_service.ApplicationReferenceNotFoundError("No resume recorded for this opportunity; choose one")
    source_email_id = opportunity.source_email_id or _source_email_id_for_record(
        db,
        owner_id=owner_id,
        record_id=opportunity.record_id,
    )
    return create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=effective_resume_id, dedupe_key=dedupe_key,
        manual_recruiter_name=recruiter.recruiter_name or "Unknown", manual_recruiter_company=recruiter.company or "Unknown",
        manual_recruiter_email=recruiter.recruiter_email or "", manual_job_title=opportunity.job_title or "Not specified",
        manual_end_client=opportunity.end_client or "", location_snapshot=opportunity.location or "",
        manual_source_note=f"Tracked from recruiter opportunity {opportunity.id}",
        recruiter_opportunity_id=opportunity.id,
        recruiter_contact_id=recruiter.id,
        source_recruiter_email_id=source_email_id,
    )


def promote_legacy_application(
    db: Session,
    legacy_application: Application,
    *,
    owner_id: str,
) -> tuple[AppTSApplication, bool]:
    if legacy_application.promoted_to_appts_application_id:
        existing = (
            db.query(AppTSApplication)
            .filter(
                AppTSApplication.owner_id == owner_id,
                AppTSApplication.id == legacy_application.promoted_to_appts_application_id,
            )
            .first()
        )
        if existing is not None:
            return existing, False
    row, created = create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=legacy_application.resume_asset_id,
        dedupe_key=f"appts_promoted:{legacy_application.id}",
        manual_recruiter_name=legacy_application.manual_recruiter_name or legacy_application.recruiter_name_snapshot or "Unknown",
        manual_recruiter_company=legacy_application.manual_recruiter_company or legacy_application.recruiter_company_snapshot or "Unknown",
        manual_recruiter_email=legacy_application.manual_recruiter_email,
        manual_recruiter_phone=legacy_application.manual_recruiter_phone,
        manual_recruiter_linkedin_url=legacy_application.manual_recruiter_linkedin_url,
        manual_job_title=legacy_application.manual_job_title or legacy_application.job_title_snapshot or "Not specified",
        manual_end_client=legacy_application.manual_end_client or legacy_application.end_client_snapshot or "",
        manual_jd_text=legacy_application.manual_jd_text,
        manual_source_note=legacy_application.manual_source_note,
        submission_method=legacy_application.submission_method,
        resume_submitted_at=legacy_application.resume_submitted_at,
        recruiter_opportunity_id=legacy_application.recruiter_opportunity_id,
        recruiter_contact_id=legacy_application.recruiter_contact_id,
    )
    legacy_application.promoted_to_appts_application_id = row.id
    return row, created
