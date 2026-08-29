from __future__ import annotations

import json
import logging
from datetime import datetime
from email.utils import parseaddr

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
    utc_now,
)
from app.services import application_service, resume_tracking_service

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
            kwargs={"record_type": "appts_application", "record_id": record_id},
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
    manual_end_client: str,
    manual_recruiter_email: str = "",
    manual_recruiter_phone: str = "",
    manual_recruiter_linkedin_url: str = "",
    manual_jd_text: str = "",
    manual_source_note: str = "",
    submission_method: str = "email",
    resume_submitted_at: datetime | None = None,
    location_snapshot: str = "",
    source_recruiter_email_id: int | None = None,
) -> tuple[AppTSApplication, bool]:
    resume = db.query(ResumeAsset).filter(ResumeAsset.owner_id == owner_id, ResumeAsset.id == resume_asset_id).first()
    if resume is None:
        raise application_service.ApplicationReferenceNotFoundError("Resume not found")
    dedupe_key = dedupe_key.strip()
    if not dedupe_key or len(dedupe_key) > 64:
        raise application_service.ApplicationValidationError("dedupe_key is required and must be at most 64 characters")
    required = (manual_recruiter_name, manual_recruiter_company, manual_job_title, manual_end_client)
    if any(not value.strip() for value in required):
        raise application_service.ApplicationValidationError("Recruiter, company, job title, and end client must not be blank")
    submitted_at = resume_submitted_at or utc_now()
    contact_id, resolved_email = _resolved_identity(db, owner_id, manual_recruiter_email)
    row = AppTSApplication(
        owner_id=owner_id, resume_asset_id=resume.id, resume_version_snapshot=resume.version,
        resume_file_name_snapshot=resume.file_name, resume_sha256_snapshot=resume.sha256,
        recruiter_name_snapshot=manual_recruiter_name.strip(), recruiter_company_snapshot=manual_recruiter_company.strip(),
        job_title_snapshot=manual_job_title.strip(), end_client_snapshot=manual_end_client.strip(), location_snapshot=location_snapshot.strip(),
        manual_recruiter_name=manual_recruiter_name.strip(), manual_recruiter_company=manual_recruiter_company.strip(),
        manual_recruiter_email=manual_recruiter_email.strip(), manual_recruiter_phone=manual_recruiter_phone.strip(),
        manual_recruiter_linkedin_url=manual_recruiter_linkedin_url.strip(), manual_job_title=manual_job_title.strip(),
        manual_end_client=manual_end_client.strip(), manual_jd_text=manual_jd_text.strip(), manual_source_note=manual_source_note.strip(),
        status="resume_shared", status_changed_at=submitted_at, resume_shared_at=submitted_at,
        resume_submission_status="submitted", resume_submitted_at=submitted_at, submission_method=submission_method.strip(),
        dedupe_key=dedupe_key, resume_skills_snapshot_json=json.dumps(resume_tracking_service.snapshot_resume_skills(resume), separators=(",", ":")),
        resume_primary_role_snapshot=(resume.primary_role or "").strip(), resolved_recruiter_contact_id=contact_id,
        resolved_recruiter_email=resolved_email, source_recruiter_email_id=source_recruiter_email_id,
        created_at=utc_now(), updated_at=utc_now(),
    )
    result = _insert(db, row)
    if result[1]:
        resume_tracking_service.compute_skill_gap(db, row, models=APPTS_MODELS)
    return result


def create_tracked_application_from_email(
    db: Session,
    email: RecruiterEmail,
    *,
    owner_id: str,
) -> tuple[AppTSApplication, bool] | None:
    if not email.resume_asset_id:
        return None
    recruiter_name, recruiter_email = parseaddr(email.sender or "")
    company = (email.company or "").strip() or "Unknown"
    return create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=email.resume_asset_id, dedupe_key=f"appts_email:{email.id}",
        manual_recruiter_name=recruiter_name.strip() or recruiter_email or "Unknown", manual_recruiter_company=company,
        manual_recruiter_email=recruiter_email, manual_job_title=(email.role or "").strip() or "Not specified",
        manual_end_client=(email.end_client or "").strip() or company, manual_source_note=f"Tracked from email {email.id}",
        resume_submitted_at=email.sent_at, location_snapshot=email.location or "",
        source_recruiter_email_id=email.id,
    )


def create_tracked_application_from_opportunity(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
    recruiter_opportunity_id: int,
    dedupe_key: str,
) -> tuple[AppTSApplication, bool]:
    opportunity = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == owner_id, RecruiterOpportunity.id == recruiter_opportunity_id).first()
    if opportunity is None:
        raise application_service.ApplicationReferenceNotFoundError("Opportunity not found")
    recruiter = db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id == opportunity.recruiter_number_id, PremiumNumberContact.deleted_at.is_(None)).first()
    if recruiter is None:
        raise application_service.ApplicationReferenceNotFoundError("Recruiter contact not found")
    return create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=resume_asset_id, dedupe_key=dedupe_key,
        manual_recruiter_name=recruiter.recruiter_name or "Unknown", manual_recruiter_company=recruiter.company or "Unknown",
        manual_recruiter_email=recruiter.recruiter_email or "", manual_job_title=opportunity.job_title or "Not specified",
        manual_end_client=opportunity.end_client or recruiter.company or "Unknown", location_snapshot=opportunity.location or "",
        manual_source_note=f"Tracked from recruiter opportunity {opportunity.id}",
    )


def promote_legacy_application(
    db: Session,
    legacy_application: Application,
    *,
    owner_id: str,
) -> tuple[AppTSApplication, bool]:
    return create_tracked_application_manual(
        db, owner_id=owner_id, resume_asset_id=legacy_application.resume_asset_id,
        dedupe_key=f"appts_promoted:{legacy_application.id}",
        manual_recruiter_name=legacy_application.manual_recruiter_name or legacy_application.recruiter_name_snapshot or "Unknown",
        manual_recruiter_company=legacy_application.manual_recruiter_company or legacy_application.recruiter_company_snapshot or "Unknown",
        manual_recruiter_email=legacy_application.manual_recruiter_email,
        manual_recruiter_phone=legacy_application.manual_recruiter_phone,
        manual_recruiter_linkedin_url=legacy_application.manual_recruiter_linkedin_url,
        manual_job_title=legacy_application.manual_job_title or legacy_application.job_title_snapshot or "Not specified",
        manual_end_client=legacy_application.manual_end_client or legacy_application.end_client_snapshot or "Unknown",
        manual_jd_text=legacy_application.manual_jd_text,
        manual_source_note=legacy_application.manual_source_note,
        submission_method=legacy_application.submission_method,
        resume_submitted_at=legacy_application.resume_submitted_at,
    )
