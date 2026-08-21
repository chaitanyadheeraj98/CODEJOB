from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy.orm import Session

from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    Application,
    ApplicationEvent,
    ApplicationInterview,
    ApplicationSuggestion,
    EmailConversation,
    EmailReplyMessage,
    PremiumNumberContact,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
    utc_now,
)
from app.services.application_service import ApplicationReferenceNotFoundError, append_event, find_duplicate_candidates
from app.skill_taxonomy import compute_intent_weighted_match, role_family_fit_score


@dataclass
class OpportunityMatch:
    opportunity_id: int
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class RecruiterReputation:
    recruiter_contact_id: int
    history_label: str
    outreach_count: int
    replies_count: int
    median_first_reply_business_days: float | None
    submissions_count: int
    interviews_after_submission_count: int
    offers_count: int
    last_active_at: datetime | None


_STATUS_CHANGE_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("offer", ("we'd like to offer", "pleased to offer", "extending an offer")),
    ("rejected", ("decided to move forward with another candidate", "not moving forward", "position has been filled")),
    ("position_closed", ("role has been closed", "position was cancelled", "req was closed")),
    ("interview_1", ("schedule a call", "set up an interview", "interview you", "technical screen")),
    ("rtr_requested", ("right to represent", "rtr", "sign the rtr", "submit your resume")),
    ("client_reviewing", ("submitted your resume", "shared your profile with the client")),
)


def _clamp01(value: float) -> float:
    return max(0.0, min(value, 1.0))


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()] if isinstance(parsed, list) else []


def _metadata(event: ApplicationEvent) -> dict[str, object]:
    try:
        value = json.loads(event.metadata_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _business_day_delta(start: datetime, end: datetime) -> float:
    if end <= start:
        return 0.0
    cursor = start.astimezone(UTC)
    finish = end.astimezone(UTC)
    seconds = 0.0
    while cursor.date() <= finish.date():
        next_day = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        segment_end = min(next_day, finish)
        if cursor.weekday() < 5:
            seconds += max(0.0, (segment_end - cursor).total_seconds())
        cursor = next_day
    return seconds / 86_400


def _business_days_ago(now: datetime, days: int) -> datetime:
    value = now
    remaining = days
    while remaining:
        value -= timedelta(days=1)
        if value.weekday() < 5:
            remaining -= 1
    return value


def compute_recruiter_reputation(
    db: Session,
    *,
    owner_id: str,
    recruiter_contact_id: int,
) -> RecruiterReputation:
    applications = (
        db.query(Application)
        .filter(
            Application.owner_id == owner_id,
            Application.recruiter_contact_id == recruiter_contact_id,
            Application.deleted_at.is_(None),
        )
        .all()
    )
    application_ids = [row.id for row in applications]
    events = (
        db.query(ApplicationEvent)
        .filter(
            ApplicationEvent.owner_id == owner_id,
            ApplicationEvent.application_id.in_(application_ids),
        )
        .order_by(ApplicationEvent.occurred_at.asc(), ApplicationEvent.id.asc())
        .all()
        if application_ids
        else []
    )
    events_by_application: dict[int, list[ApplicationEvent]] = {}
    for event in events:
        events_by_application.setdefault(event.application_id, []).append(event)

    replies: set[int] = set()
    offers: set[int] = {row.id for row in applications if row.status in {"offer", "hired"}}
    reply_days: list[float] = []
    for application in applications:
        last_contact: datetime | None = None
        first_reply: datetime | None = None
        for event in events_by_application.get(application.id, []):
            metadata = _metadata(event)
            target_status = metadata.get("to") if event.event_type == "status_changed" else None
            if target_status == "contacted":
                last_contact = event.occurred_at
            if target_status == "offer":
                offers.add(application.id)
            if (target_status == "recruiter_responded" or event.event_type == "recruiter_replied") and first_reply is None:
                replies.add(application.id)
                first_reply = event.occurred_at
                if last_contact is not None:
                    reply_days.append(_business_day_delta(last_contact, first_reply))

    interview_application_ids = {
        row[0]
        for row in (
            db.query(ApplicationInterview.application_id)
            .filter(
                ApplicationInterview.owner_id == owner_id,
                ApplicationInterview.application_id.in_(application_ids),
                ApplicationInterview.deleted_at.is_(None),
            )
            .distinct()
            .all()
            if application_ids
            else []
        )
    }
    outreach_count = sum(row.status != "matched" for row in applications)
    submissions_count = sum(row.submitted_to_client_at is not None for row in applications)
    interviews_after_submission_count = sum(
        row.submitted_to_client_at is not None and row.id in interview_application_ids
        for row in applications
    )
    return RecruiterReputation(
        recruiter_contact_id=recruiter_contact_id,
        history_label="limited_history" if outreach_count < 3 else "established",
        outreach_count=outreach_count,
        replies_count=len(replies),
        median_first_reply_business_days=round(float(median(reply_days)), 2) if reply_days else None,
        submissions_count=submissions_count,
        interviews_after_submission_count=interviews_after_submission_count,
        offers_count=len(offers),
        last_active_at=max((event.occurred_at for event in events), default=None),
    )


def _work_mode_fit(preference: str, work_mode: str) -> float:
    preferred = re.sub(r"[^a-z0-9]+", "", (preference or "").lower())
    actual = re.sub(r"[^a-z0-9]+", "", (work_mode or "").lower())
    if preferred in {"", "any"}:
        return 1.0
    if not actual:
        return 0.6
    if preferred in actual or actual in preferred:
        return 1.0
    return 0.3


def _visa_fit(restrictions: str, authorizations: list[str]) -> float:
    restriction = re.sub(r"\s+", " ", (restrictions or "").strip().lower())
    normalized_authorizations = [re.sub(r"[^a-z0-9]+", "", value.lower()) for value in authorizations]
    if not restriction or not normalized_authorizations:
        return 0.6
    compact = re.sub(r"[^a-z0-9]+", "", restriction)
    if "noc2c" in compact and "c2c" in normalized_authorizations:
        return 0.2
    sponsorship_needed = {"h1b", "opt", "cpt", "tn"}
    if "nosponsorship" in compact and sponsorship_needed.intersection(normalized_authorizations):
        return 0.2
    if any(value and value in compact for value in normalized_authorizations):
        return 1.0
    if any(token in restriction for token in ("only", "required", "no c2c", "no sponsorship", "citizen", "green card")):
        return 0.2
    return 0.6


def rank_opportunities_for_resume(
    db: Session,
    *,
    owner_id: str,
    resume_asset_id: int,
    limit: int = 25,
    exclude_already_applied: bool = True,
) -> list[OpportunityMatch]:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == owner_id, ResumeAsset.id == resume_asset_id)
        .first()
    )
    if resume is None:
        raise ApplicationReferenceNotFoundError("Resume not found")

    query = (
        db.query(RecruiterOpportunity)
        .join(PremiumNumberContact, PremiumNumberContact.id == RecruiterOpportunity.recruiter_number_id)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.status.not_in(("Closed", "Not Interested")),
            PremiumNumberContact.owner_id == owner_id,
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.do_not_work_again.is_(False),
        )
    )
    if exclude_already_applied:
        already_applied = db.query(Application.id).filter(
            Application.owner_id == owner_id,
            Application.resume_asset_id == resume_asset_id,
            Application.recruiter_opportunity_id == RecruiterOpportunity.id,
            Application.deleted_at.is_(None),
        ).exists()
        query = query.filter(~already_applied)
    opportunities = query.all()

    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    remote_preference = user_settings.remote_preference if user_settings else "any"
    authorizations = _json_list(user_settings.candidate_work_authorizations_json if user_settings else None)
    employment_preferences = {
        value.lower() for value in _json_list(user_settings.preferred_employment_types_json if user_settings else None)
    }
    minimum_rate = user_settings.preferred_minimum_rate if user_settings else None
    reputations = {
        recruiter_id: compute_recruiter_reputation(
            db,
            owner_id=owner_id,
            recruiter_contact_id=recruiter_id,
        )
        for recruiter_id in {row.recruiter_number_id for row in opportunities}
    }
    now = utc_now()
    matches: list[OpportunityMatch] = []
    for opportunity in opportunities:
        intent = compute_intent_weighted_match(
            jd_role=opportunity.job_title,
            jd_skills_text=opportunity.extracted_skills,
            resume_skills_text=resume.skills_text,
        )
        fit, _ = role_family_fit_score(
            jd_role_family=intent.jd_role_family,
            resume_role_family=intent.resume_role_family,
            role_alignment_score=intent.score,
            foundation_score=intent.foundation_score,
            jd_priority_score=intent.specialization_score,
        )
        work_mode_fit = _work_mode_fit(remote_preference, opportunity.work_mode)
        role_location_fit = (work_mode_fit + _visa_fit(opportunity.visa_restrictions, authorizations)) / 2
        age_days = max(0.0, (now - opportunity.received_at).total_seconds() / 86_400) if opportunity.received_at else None
        freshness = 0.5 if age_days is None else (1.0 if age_days <= 2 else max(0.2, 1 - ((age_days - 2) / 28) * 0.8))
        reputation = reputations[opportunity.recruiter_number_id]
        responsiveness = (
            0.5
            if reputation.history_label == "limited_history"
            else _clamp01(reputation.replies_count / max(reputation.outreach_count, 1))
        )
        employment_type = (opportunity.employment_type or "").strip().lower()
        employment_fit = (
            0.5
            if not employment_preferences or not employment_type
            else 1.0 if employment_type in employment_preferences else 0.3
        )
        rate_fit = 0.5
        if minimum_rate is not None and opportunity.rate_amount is not None:
            rate_fit = 1.0 if opportunity.rate_amount >= minimum_rate else _clamp01(
                (opportunity.rate_amount / max(minimum_rate, 0.01) - 0.5) * 2
            )
        preference_fit = (employment_fit + rate_fit) / 2
        risk_penalty = 0.0
        if opportunity.job_confidence == "low":
            risk_penalty += 0.15
        elif opportunity.job_confidence == "unknown":
            risk_penalty += 0.05
        if opportunity.end_client_confirmed is False:
            risk_penalty += 0.05
        if find_duplicate_candidates(
            db,
            owner_id=owner_id,
            end_client=opportunity.end_client,
            job_title=opportunity.job_title,
            exclude_application_id=0,
        ):
            risk_penalty += 0.10
        score = round(
            _clamp01(
                0.45 * fit
                + 0.15 * role_location_fit
                + 0.15 * freshness
                + 0.15 * responsiveness
                + 0.10 * preference_fit
                - risk_penalty
            ) * 100,
            1,
        )
        reasons: list[str] = []
        if intent.matched_specialization_skills:
            reasons.append(f"Strong {', '.join(intent.matched_specialization_skills[:3])} overlap")
        else:
            reasons.append("Limited role-specific skill overlap")
        if work_mode_fit == 1.0 and remote_preference.lower() not in {"", "any"}:
            reasons.append(f"{opportunity.work_mode or 'Work mode'} matches your preference")
        elif work_mode_fit < 0.5:
            reasons.append("Work mode may not match your preference")
        if reputation.history_label == "limited_history":
            reasons.append("Limited history with this recruiter")
        else:
            reasons.append(f"Recruiter replied to {reputation.replies_count} of {reputation.outreach_count} outreach attempts")
        reasons.append("Job posting date unknown" if age_days is None else f"Job posted {int(age_days)} days ago")
        matches.append(OpportunityMatch(opportunity_id=opportunity.id, score=score, reasons=reasons[:4]))

    matches.sort(key=lambda item: (-item.score, item.opportunity_id))
    return matches[: max(1, min(int(limit), 100))]


def detect_status_change_signal(reply_body: str) -> tuple[str, str] | None:
    normalized = re.sub(r"\s+", " ", (reply_body or "").strip().lower())
    for suggested_status, phrases in _STATUS_CHANGE_SIGNALS:
        for phrase in phrases:
            if phrase in normalized:
                return suggested_status, phrase
    return None


def _pending_suggestion(db: Session, owner_id: str, application_id: int, suggestion_type: str) -> bool:
    return db.query(ApplicationSuggestion.id).filter(
        ApplicationSuggestion.owner_id == owner_id,
        ApplicationSuggestion.application_id == application_id,
        ApplicationSuggestion.suggestion_type == suggestion_type,
        ApplicationSuggestion.status == "pending",
    ).first() is not None


def correlate_reply_to_application(
    db: Session,
    *,
    owner_id: str,
    reply_message_id: int,
) -> ApplicationSuggestion | None:
    reply = db.query(EmailReplyMessage).filter(
        EmailReplyMessage.owner_id == owner_id,
        EmailReplyMessage.id == reply_message_id,
        EmailReplyMessage.direction == "inbound",
    ).first()
    if reply is None:
        return None
    conversation = db.query(EmailConversation).filter(
        EmailConversation.owner_id == owner_id,
        EmailConversation.id == reply.conversation_id,
    ).first()
    if conversation is None:
        return None
    opportunity = db.query(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == owner_id,
        RecruiterOpportunity.source_email_id == conversation.root_recruiter_email_id,
    ).order_by(RecruiterOpportunity.id.asc()).first()
    if opportunity is None:
        return None
    applications = db.query(Application).filter(
        Application.owner_id == owner_id,
        Application.recruiter_opportunity_id == opportunity.id,
        Application.deleted_at.is_(None),
        Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
    ).limit(2).all()
    if len(applications) != 1:
        return None
    application = applications[0]
    reply_already_logged = any(
        _metadata(event).get("reply_message_id") == reply.id
        for event in db.query(ApplicationEvent).filter(
            ApplicationEvent.owner_id == owner_id,
            ApplicationEvent.application_id == application.id,
            ApplicationEvent.event_type == "recruiter_replied",
        ).all()
    )
    if not reply_already_logged:
        append_event(
            db,
            application,
            event_type="recruiter_replied",
            event_source="system",
            note=reply.snippet,
            linked_recruiter_email_id=conversation.root_recruiter_email_id,
            metadata={"reply_message_id": reply.id},
        )
        application.last_contact_at = utc_now()

    signal = detect_status_change_signal(reply.body)
    if signal is None or signal[0] == application.status or _pending_suggestion(db, owner_id, application.id, "status_change"):
        return None
    suggestion = ApplicationSuggestion(
        owner_id=owner_id,
        application_id=application.id,
        suggestion_type="status_change",
        status="pending",
        confidence="high",
        reply_message_id=reply.id,
        recruiter_email_id=conversation.root_recruiter_email_id,
        suggested_status=signal[0],
        reason=f"Matched phrase: '{signal[1]}'",
        created_at=utc_now(),
    )
    db.add(suggestion)
    return suggestion


def _create_reminder(
    db: Session,
    application: Application,
    *,
    suggestion_type: str,
    reason: str,
    next_action_type: str | None = None,
    next_action_at: datetime | None = None,
) -> ApplicationSuggestion | None:
    if _pending_suggestion(db, application.owner_id, application.id, suggestion_type):
        return None
    suggestion = ApplicationSuggestion(
        owner_id=application.owner_id,
        application_id=application.id,
        suggestion_type=suggestion_type,
        status="pending",
        confidence="high",
        suggested_next_action_type=next_action_type,
        suggested_next_action_at=next_action_at,
        reason=reason,
        created_at=utc_now(),
    )
    db.add(suggestion)
    return suggestion


def generate_reminder_sweep_suggestions(db: Session, *, owner_id: str) -> list[ApplicationSuggestion]:
    now = utc_now()
    applications = db.query(Application).filter(
        Application.owner_id == owner_id,
        Application.deleted_at.is_(None),
        Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
    ).all()
    application_ids = [row.id for row in applications]
    completed_interview_ids = {
        row[0]
        for row in (
            db.query(ApplicationInterview.application_id)
            .filter(
                ApplicationInterview.owner_id == owner_id,
                ApplicationInterview.application_id.in_(application_ids),
                ApplicationInterview.deleted_at.is_(None),
                ApplicationInterview.result == "completed",
                ApplicationInterview.follow_up_task_note == "",
                ApplicationInterview.updated_at <= _business_days_ago(now, 1),
            )
            .distinct()
            .all()
            if application_ids
            else []
        )
    }
    created: list[ApplicationSuggestion] = []
    for application in applications:
        next_action_reason: str | None = None
        next_action_type: str | None = None
        if (
            application.status == "contacted"
            and application.next_action_at is None
            and application.status_changed_at <= _business_days_ago(now, 3)
        ):
            next_action_type = "Follow up with recruiter"
            next_action_reason = "No reply in 3 business days after first outreach"
        elif (
            application.status == "submitted_to_client"
            and application.next_action_at is None
            and application.status_changed_at <= _business_days_ago(now, 6)
        ):
            next_action_type = "Request status update"
            next_action_reason = "No update in 6 business days since submission"
        elif application.id in completed_interview_ids:
            next_action_type = "Send thank-you / follow-up"
            next_action_reason = "Interview completed without a follow-up task"
        if next_action_reason:
            suggestion = _create_reminder(
                db,
                application,
                suggestion_type="next_action",
                reason=next_action_reason,
                next_action_type=next_action_type,
                next_action_at=now,
            )
            if suggestion:
                created.append(suggestion)
        if application.status_changed_at <= now - timedelta(days=21):
            suggestion = _create_reminder(
                db,
                application,
                suggestion_type="stale_prompt",
                reason="No activity in 3 weeks - close or continue?",
            )
            if suggestion:
                created.append(suggestion)
    return created
