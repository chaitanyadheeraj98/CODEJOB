from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from statistics import median

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    REJECTION_DETAIL_TAG_VALUES,
    RESUME_SUBMISSION_STATUS_VALUES,
    Application,
    ApplicationOutreachMessage,
    ApplicationSkillGapSnapshot,
    ApplicationSuggestion,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    utc_now,
)
from app.parsing.jd_requirements import ParsedJDRequirements, requirements_from_payload
from app.skill_taxonomy import compute_intent_weighted_match, detect_role_family
from app.services import application_service


USER_RESUME_STATUS_VALUES = {
    'viewed',
    'shortlisted',
    'offered',
    'hired',
    'rejected',
    'withdrawn',
}
STATUS_TO_APPLICATION_STATUS = {
    'offered': 'offer',
    'hired': 'hired',
    'rejected': 'rejected',
    'withdrawn': 'withdrawn',
}


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


def _dump(value: object) -> str:
    return json.dumps(value, separators=(',', ':'))


def _dedupe_strings(values: list[object]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = str(value or '').strip()
        key = re.sub(r'\s+', ' ', cleaned).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def snapshot_resume_skills(resume: ResumeAsset) -> list[str]:
    structured = _dedupe_strings(_json_list(resume.structured_skills_json))
    if structured:
        return structured
    return _dedupe_strings(re.split(r'[,;\n]+', resume.skills_text or ''))


def _validate_dedupe_key(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 64:
        raise application_service.ApplicationValidationError(
            'dedupe_key is required and must be at most 64 characters'
        )
    return cleaned


def create_manual_application(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
    dedupe_key: str,
    manual_recruiter_name: str,
    manual_recruiter_company: str,
    manual_recruiter_email: str = '',
    manual_recruiter_phone: str = '',
    manual_recruiter_linkedin_url: str = '',
    manual_job_title: str,
    manual_end_client: str,
    manual_jd_text: str = '',
    manual_source_note: str = '',
    submission_method: str = 'email',
    resume_submitted_at: datetime | None = None,
) -> tuple[Application, bool]:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.id == resume_asset_id)
        .first()
    )
    if resume is None:
        raise application_service.ApplicationReferenceNotFoundError('Resume not found')
    dedupe_key = _validate_dedupe_key(dedupe_key)
    required = {
        'manual_recruiter_name': manual_recruiter_name,
        'manual_recruiter_company': manual_recruiter_company,
        'manual_job_title': manual_job_title,
        'manual_end_client': manual_end_client,
    }
    missing = [name for name, value in required.items() if not value.strip()]
    if missing:
        raise application_service.ApplicationValidationError(
            f'{", ".join(missing)} must not be blank'
        )
    submission_method = submission_method.strip()
    if not submission_method or len(submission_method) > 20:
        raise application_service.ApplicationValidationError(
            'submission_method is required and must be at most 20 characters'
        )

    submitted_at = resume_submitted_at or utc_now()
    application = Application(
        owner_id=owner_id,
        resume_asset_id=resume.id,
        resume_version_snapshot=resume.version,
        resume_file_name_snapshot=resume.file_name,
        resume_sha256_snapshot=resume.sha256,
        recruiter_opportunity_id=None,
        recruiter_contact_id=None,
        recruiter_name_snapshot=manual_recruiter_name.strip(),
        recruiter_company_snapshot=manual_recruiter_company.strip(),
        job_title_snapshot=manual_job_title.strip(),
        end_client_snapshot=manual_end_client.strip(),
        manual_recruiter_name=manual_recruiter_name.strip(),
        manual_recruiter_company=manual_recruiter_company.strip(),
        manual_recruiter_email=manual_recruiter_email.strip(),
        manual_recruiter_phone=manual_recruiter_phone.strip(),
        manual_recruiter_linkedin_url=manual_recruiter_linkedin_url.strip(),
        manual_job_title=manual_job_title.strip(),
        manual_end_client=manual_end_client.strip(),
        manual_jd_text=manual_jd_text.strip(),
        manual_source_note=manual_source_note.strip(),
        status='resume_shared',
        status_changed_at=submitted_at,
        resume_shared_at=submitted_at,
        resume_submission_status='submitted',
        resume_submitted_at=submitted_at,
        submission_method=submission_method,
        dedupe_key=dedupe_key,
        resume_skills_snapshot_json=_dump(snapshot_resume_skills(resume)),
        resume_primary_role_snapshot=(resume.primary_role or '').strip(),
        created_at=utc_now(),
        updated_at=utc_now(),
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

    application_service.append_event(
        db,
        application,
        event_type='created',
        event_source='user',
    )
    application_service.append_event(
        db,
        application,
        event_type='manual_submission_logged',
        event_source='user',
        note=application.manual_source_note,
        metadata={'resume_submitted_at': submitted_at.isoformat(), 'submission_method': submission_method},
    )
    compute_skill_gap(db, application)
    return application, True


def _validated_tags(tags: list[object] | None) -> list[tuple[str, str]]:
    validated: list[tuple[str, str]] = []
    for tag in tags or []:
        if hasattr(tag, 'model_dump'):
            tag = tag.model_dump()
        if not isinstance(tag, dict):
            raise application_service.ApplicationValidationError('Invalid rejection detail tag')
        category = str(tag.get('category') or '').strip()
        value = str(tag.get('value') or '').strip()
        if category not in REJECTION_DETAIL_TAG_VALUES:
            raise application_service.ApplicationValidationError('Invalid rejection detail tag category')
        validated.append((category, value))
    return validated


def _merge_user_tags(application: Application, tags: list[tuple[str, str]]) -> None:
    existing = [tag for tag in _json_list(application.rejection_detail_tags_json) if isinstance(tag, dict)]
    now = utc_now().isoformat()
    for category, value in tags:
        match = next(
            (
                tag
                for tag in existing
                if str(tag.get('category') or '') == category
                and str(tag.get('value') or '').strip().casefold() == value.casefold()
            ),
            None,
        )
        if match is not None:
            if str(match.get('source') or '') == 'ai':
                match['confirmed_at'] = now
            else:
                match.update({'source': 'user', 'confirmed_at': now})
            continue
        existing.append(
            {
                'category': category,
                'value': value,
                'source': 'user',
                'confirmed_at': now,
            }
        )
    application.rejection_detail_tags_json = _dump(existing)


def update_resume_submission_status(
    db: Session,
    application: Application,
    *,
    new_status: str,
    rejection_detail_tags: list[object] | None = None,
    note: str | None = None,
    force: bool = False,
) -> Application:
    if new_status not in USER_RESUME_STATUS_VALUES:
        if new_status == 'interview_scheduled':
            raise application_service.ApplicationValidationError(
                'Log an interview through the interviews endpoint'
            )
        raise application_service.ApplicationValidationError('Invalid resume submission status')
    tags = _validated_tags(rejection_detail_tags)
    old_resume_status = application.resume_submission_status
    if not force and (
        RESUME_SUBMISSION_STATUS_VALUES.index(new_status)
        < RESUME_SUBMISSION_STATUS_VALUES.index(old_resume_status)
    ):
        raise application_service.ApplicationValidationError(
            'Resume submission status cannot move backward without force'
        )

    mapped_status = STATUS_TO_APPLICATION_STATUS.get(new_status)
    if mapped_status is not None:
        if application.status != mapped_status or old_resume_status != new_status:
            application_service.update_status(
                db,
                application,
                new_status=mapped_status,
                note=note,
            )
        if force and application.resume_submission_status != new_status:
            previous = application.resume_submission_status
            application.resume_submission_status = new_status
            application_service.append_event(
                db,
                application,
                event_type='resume_submission_status_changed',
                event_source='user',
                note=note or '',
                metadata={'from': previous, 'to': new_status, 'trigger': 'user_correction'},
            )
    elif old_resume_status != new_status:
        application.resume_submission_status = new_status
        milestones = _json_dict(application.milestones_reached_json)
        milestones.setdefault(new_status, utc_now().isoformat())
        application.milestones_reached_json = _dump(milestones)
        application_service.append_event(
            db,
            application,
            event_type='resume_submission_status_changed',
            event_source='user',
            note=note or '',
            metadata={
                'from': old_resume_status,
                'to': new_status,
                'trigger': 'user_correction' if force else 'user',
            },
        )
    if tags:
        _merge_user_tags(application, tags)
    return application


def _structured_requirements(
    db: Session,
    application: Application,
) -> tuple[ParsedJDRequirements | None, str]:
    if application.recruiter_opportunity_id is None:
        return None, application.manual_jd_text or ''
    opportunity = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == application.owner_id,
            RecruiterOpportunity.id == application.recruiter_opportunity_id,
        )
        .first()
    )
    if opportunity is None:
        return None, ''
    if opportunity.source_email_id is not None:
        email = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == application.owner_id,
                RecruiterEmail.id == opportunity.source_email_id,
            )
            .first()
        )
        if email is not None:
            try:
                payload = json.loads(email.parser_details_json or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            structured_payload = payload.get('structured_requirements') if isinstance(payload, dict) else None
            if isinstance(structured_payload, dict):
                requirements = requirements_from_payload(structured_payload)
                if requirements.required_groups or requirements.preferred_groups:
                    return requirements, opportunity.extracted_skills or ''
    return None, opportunity.extracted_skills or ''


def _normalized_lookup(values: list[str]) -> dict[str, str]:
    return {re.sub(r'\s+', ' ', value).strip().casefold(): value for value in values if value.strip()}


def _diff_skills(requirements: list[str], resume_skills: list[str]) -> tuple[list[str], list[str]]:
    resume_lookup = _normalized_lookup(resume_skills)
    required_lookup = _normalized_lookup(requirements)
    matched = [label for key, label in required_lookup.items() if key in resume_lookup]
    missing = [label for key, label in required_lookup.items() if key not in resume_lookup]
    return matched, missing


def compute_skill_gap(
    db: Session,
    application: Application,
    *,
    force_recompute: bool = False,
) -> ApplicationSkillGapSnapshot:
    existing = (
        db.query(ApplicationSkillGapSnapshot)
        .filter(
            ApplicationSkillGapSnapshot.owner_id == application.owner_id,
            ApplicationSkillGapSnapshot.application_id == application.id,
        )
        .first()
    )
    if existing is not None and not force_recompute:
        return existing

    resume_skills = _dedupe_strings(_json_list(application.resume_skills_snapshot_json))
    requirements, fallback_text = _structured_requirements(db, application)
    if requirements is not None:
        required = _dedupe_strings(
            [skill.canonical_name for group in requirements.required_groups for skill in group.skills]
        )
        preferred = _dedupe_strings(
            [skill.canonical_name for group in requirements.preferred_groups for skill in group.skills]
        )
        matched_required, missing_required = _diff_skills(required, resume_skills)
        matched_preferred, missing_preferred = _diff_skills(preferred, resume_skills)
        source = 'structured'
    else:
        breakdown = compute_intent_weighted_match(
            jd_role=application.job_title_snapshot or application.manual_job_title,
            jd_skills_text=fallback_text,
            resume_skills_text=', '.join(resume_skills),
        )
        matched_required = list(breakdown.matched_specialization_skills)
        missing_required = list(breakdown.missing_specialization_skills)
        matched_preferred = []
        missing_preferred = []
        source = 'fallback_text'

    snapshot = existing or ApplicationSkillGapSnapshot(
        owner_id=application.owner_id,
        application_id=application.id,
    )
    snapshot.source = source
    snapshot.matched_required_json = _dump(matched_required)
    snapshot.missing_required_json = _dump(missing_required)
    snapshot.matched_preferred_json = _dump(matched_preferred)
    snapshot.missing_preferred_json = _dump(missing_preferred)
    snapshot.computed_at = utc_now()
    if existing is None:
        db.add(snapshot)
    return snapshot


def _parse_milestone(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _rate(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _top(counter: Counter[str], *, limit: int = 5) -> list[dict[str, object]]:
    return [{'value': value, 'count': count} for value, count in counter.most_common(limit)]


def resume_funnel_metrics(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
) -> dict[str, object]:
    applications = (
        db.query(Application)
        .filter(
            Application.owner_id == owner_id,
            Application.resume_asset_id == resume_asset_id,
            Application.deleted_at.is_(None),
        )
        .all()
    )
    submitted = [row for row in applications if row.resume_submission_status != 'not_submitted']
    total = len(submitted)
    milestone_names = ('viewed', 'shortlisted', 'interview_scheduled', 'offered', 'hired')
    counts = Counter[str]()
    elapsed: dict[str, list[float]] = defaultdict(list)
    rejection_reasons: Counter[str] = Counter()
    missing_skills: Counter[str] = Counter()
    for application in submitted:
        milestones = _json_dict(application.milestones_reached_json)
        for name in milestone_names:
            reached_at = _parse_milestone(milestones.get(name))
            if reached_at is None:
                continue
            counts[name] += 1
            if application.resume_submitted_at is not None and name in {
                'shortlisted',
                'interview_scheduled',
                'offered',
            }:
                submitted_at = application.resume_submitted_at
                if submitted_at.tzinfo is None:
                    submitted_at = submitted_at.replace(tzinfo=UTC)
                elapsed[name].append(max(0.0, (reached_at - submitted_at).total_seconds() / 86400))
        if application.status == 'rejected' or application.resume_submission_status == 'rejected':
            rejection_reasons[application.closed_reason_code or 'unspecified'] += 1
        per_application_missing = {
            str(tag.get('value') or '').strip()
            for tag in _json_list(application.rejection_detail_tags_json)
            if isinstance(tag, dict)
            and tag.get('category') == 'missing_skill'
            and str(tag.get('value') or '').strip()
        }
        snapshot = (
            db.query(ApplicationSkillGapSnapshot)
            .filter(
                ApplicationSkillGapSnapshot.owner_id == owner_id,
                ApplicationSkillGapSnapshot.application_id == application.id,
            )
            .first()
        )
        if snapshot is not None:
            per_application_missing.update(
                str(value).strip()
                for value in _json_list(snapshot.missing_required_json)
                if str(value).strip()
            )
        missing_skills.update(per_application_missing)

    accepted = sum(
        1
        for application in submitted
        if any(
            name in _json_dict(application.milestones_reached_json)
            for name in ('shortlisted', 'interview_scheduled', 'offered', 'hired')
        )
    )
    rejected = sum(1 for row in submitted if row.resume_submission_status == 'rejected')
    return {
        'resume_asset_id': resume_asset_id,
        'total_submissions': total,
        'not_submitted_count': len(applications) - total,
        'view_rate': _rate(counts['viewed'], total),
        'shortlist_rate': _rate(counts['shortlisted'], total),
        'interview_rate': _rate(counts['interview_scheduled'], total),
        'offer_rate': _rate(counts['offered'], total),
        'hire_rate': _rate(counts['hired'], total),
        'rejection_rate': _rate(rejected, total),
        'acceptance_rate': _rate(accepted, total),
        'median_days_to_shortlist': median(elapsed['shortlisted']) if elapsed['shortlisted'] else None,
        'median_days_to_interview': median(elapsed['interview_scheduled']) if elapsed['interview_scheduled'] else None,
        'median_days_to_offer': median(elapsed['offered']) if elapsed['offered'] else None,
        'top_rejection_reasons': _top(rejection_reasons),
        'top_missing_skills': _top(missing_skills),
    }


def resume_performance_summary(db: Session, *, owner_id: str) -> list[dict[str, object]]:
    resumes = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == owner_id)
        .order_by(ResumeAsset.updated_at.desc(), ResumeAsset.id.desc())
        .all()
    )
    return [
        {
            'resume': resume,
            'submission_count': (metrics := resume_funnel_metrics(
                db,
                owner_id=owner_id,
                resume_asset_id=resume.id,
            ))['total_submissions'],
            'acceptance_rate': metrics['acceptance_rate'],
        }
        for resume in resumes
    ]


def _pending_suggestion(
    db: Session,
    *,
    owner_id: str,
    application_id: int,
    suggestion_type: str,
) -> bool:
    return (
        db.query(ApplicationSuggestion.id)
        .filter(
            ApplicationSuggestion.owner_id == owner_id,
            ApplicationSuggestion.application_id == application_id,
            ApplicationSuggestion.suggestion_type == suggestion_type,
            ApplicationSuggestion.status == 'pending',
        )
        .first()
        is not None
    )


def _new_suggestion(
    db: Session,
    application: Application,
    *,
    suggestion_type: str,
    reason: str,
    payload: dict[str, object],
) -> ApplicationSuggestion | None:
    if _pending_suggestion(
        db,
        owner_id=application.owner_id,
        application_id=application.id,
        suggestion_type=suggestion_type,
    ):
        return None
    suggestion = ApplicationSuggestion(
        owner_id=application.owner_id,
        application_id=application.id,
        suggestion_type=suggestion_type,
        status='pending',
        confidence='high',
        reason=reason,
        payload_json=_dump(payload),
        created_at=utc_now(),
    )
    db.add(suggestion)
    return suggestion


def generate_new_variant_needed_suggestions(
    db: Session,
    *,
    owner_id: str,
) -> list[ApplicationSuggestion]:
    rows = (
        db.query(Application)
        .filter(
            Application.owner_id == owner_id,
            Application.deleted_at.is_(None),
            Application.status == 'rejected',
        )
        .order_by(Application.created_at.asc(), Application.id.asc())
        .all()
    )
    groups: dict[tuple[int, str, str], list[Application]] = defaultdict(list)
    labels: dict[tuple[int, str, str], str] = {}
    for application in rows:
        snapshot = (
            db.query(ApplicationSkillGapSnapshot)
            .filter(
                ApplicationSkillGapSnapshot.owner_id == owner_id,
                ApplicationSkillGapSnapshot.application_id == application.id,
            )
            .first()
        )
        if snapshot is None:
            continue
        family = detect_role_family(application.job_title_snapshot, '')
        for skill in _dedupe_strings(_json_list(snapshot.missing_required_json)):
            key = (application.resume_asset_id, family, skill.casefold())
            groups[key].append(application)
            labels[key] = skill
    created: list[ApplicationSuggestion] = []
    for key, applications in groups.items():
        if len(applications) < 3:
            continue
        application = applications[-1]
        skill = labels[key]
        suggestion = _new_suggestion(
            db,
            application,
            suggestion_type='new_variant_needed',
            reason=f'{skill} was missing in {len(applications)} rejected {key[1]} submissions.',
            payload={
                'resume_asset_id': key[0],
                'missing_skill': skill,
                'occurrence_count': len(applications),
                'job_title_family': key[1],
            },
        )
        if suggestion is not None:
            created.append(suggestion)
    return created


def generate_email_positioning_suggestions(
    db: Session,
    *,
    owner_id: str,
) -> list[ApplicationSuggestion]:
    rows = (
        db.query(Application)
        .filter(Application.owner_id == owner_id, Application.deleted_at.is_(None))
        .order_by(Application.created_at.asc(), Application.id.asc())
        .all()
    )
    groups: dict[tuple[int, str, str], list[Application]] = defaultdict(list)
    for application in rows:
        recruiter_key = str(
            application.recruiter_contact_id
            or application.manual_recruiter_email
            or application.manual_recruiter_name
            or 'unknown'
        ).casefold()
        family = detect_role_family(application.job_title_snapshot, '')
        groups[(application.resume_asset_id, recruiter_key, family)].append(application)
    created: list[ApplicationSuggestion] = []
    for key, applications in groups.items():
        tagged = [
            application
            for application in applications
            if application.status in APPLICATION_CLOSED_STATUS_VALUES
            and any(
                isinstance(tag, dict) and tag.get('category') == 'email_positioning'
                for tag in _json_list(application.rejection_detail_tags_json)
            )
        ]
        submitted_only = [
            application for application in applications
            if application.resume_submission_status == 'submitted'
        ]
        if len(tagged) < 2 and len(submitted_only) < 3:
            continue
        application = (tagged or submitted_only)[-1]
        message = (
            db.query(ApplicationOutreachMessage)
            .filter(
                ApplicationOutreachMessage.owner_id == owner_id,
                ApplicationOutreachMessage.application_id == application.id,
                ApplicationOutreachMessage.message_kind == 'submission_to_recruiter',
            )
            .order_by(ApplicationOutreachMessage.sent_at.desc(), ApplicationOutreachMessage.id.desc())
            .first()
        )
        if message is None:
            continue
        suggestion = _new_suggestion(
            db,
            application,
            suggestion_type='email_positioning',
            reason='Repeated submissions to this recruiter and role family did not gain traction.',
            payload={
                'resume_asset_id': key[0],
                'last_outreach_message_id': message.id,
            },
        )
        if suggestion is not None:
            created.append(suggestion)
    return created


def generate_skill_gap_pattern_suggestions(
    db: Session,
    *,
    owner_id: str,
) -> list[ApplicationSuggestion]:
    created: list[ApplicationSuggestion] = []
    resumes = db.query(ResumeAsset).filter(ResumeAsset.owner_id == owner_id).all()
    for resume in resumes:
        metrics = resume_funnel_metrics(db, owner_id=owner_id, resume_asset_id=resume.id)
        top_missing = list(metrics['top_missing_skills'])[:3]
        if not top_missing:
            continue
        application = (
            db.query(Application)
            .filter(
                Application.owner_id == owner_id,
                Application.resume_asset_id == resume.id,
                Application.deleted_at.is_(None),
            )
            .order_by(Application.created_at.desc(), Application.id.desc())
            .first()
        )
        if application is None:
            continue
        previous = (
            db.query(ApplicationSuggestion)
            .filter(
                ApplicationSuggestion.owner_id == owner_id,
                ApplicationSuggestion.suggestion_type == 'skill_gap_pattern',
            )
            .order_by(ApplicationSuggestion.created_at.desc(), ApplicationSuggestion.id.desc())
            .all()
        )
        top_skill = str(top_missing[0].get('value') or '').casefold()
        unchanged = any(
            int(_json_dict(row.payload_json).get('resume_asset_id') or 0) == resume.id
            and str(
                (
                    (_json_dict(row.payload_json).get('top_missing_skills') or [{}])[0]
                    if isinstance(_json_dict(row.payload_json).get('top_missing_skills'), list)
                    else {}
                ).get('value')
                or ''
            ).casefold() == top_skill
            for row in previous
        )
        if unchanged:
            continue
        suggestion = _new_suggestion(
            db,
            application,
            suggestion_type='skill_gap_pattern',
            reason=f'The most common missing skill for this resume is {top_missing[0]["value"]}.',
            payload={'resume_asset_id': resume.id, 'top_missing_skills': top_missing},
        )
        if suggestion is not None:
            created.append(suggestion)
    return created


def generate_resume_tracking_suggestions(
    db: Session,
    *,
    owner_id: str,
) -> list[ApplicationSuggestion]:
    return [
        *generate_new_variant_needed_suggestions(db, owner_id=owner_id),
        *generate_email_positioning_suggestions(db, owner_id=owner_id),
        *generate_skill_gap_pattern_suggestions(db, owner_id=owner_id),
    ]
