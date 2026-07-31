from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from app.ai.resume_context_attribution import RESUME_CONTEXT_MISSING, RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision
from app.services import policy_service
from app.phase0 import _parse_salary_floor


@dataclass(frozen=True)
class QueuePreparationDependencies:
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    hard_filter_check: Callable[
        [dict[str, str | int | bool], UserSettings, Mapping[str, Any], Mapping[str, Any] | None],
        tuple[bool, str],
    ]
    compute_blended_ai_score: Callable[..., tuple[float, str, str, str | None, str | None, Any]]
    policy_f2f_block: Callable[[dict[str, str | int | bool], Mapping[str, Any]], tuple[bool, str]]
    evaluate_routing_policy: Callable[..., RoutingDecision]
    greeting_from_to_contact: Callable[[str | None, str], str]
    build_user_fallback_draft: Callable[..., str]
    generate_reply_with_ai_or_fallback: Callable[..., Any]


@dataclass(frozen=True)
class QueuePreparationRequest:
    db: Any
    owner_id: str
    sender: str
    subject: str
    body: str
    snippet: str
    user_settings: UserSettings
    effective_policy: Mapping[str, Any]
    threshold: float
    model_name: str
    scoring_resume: ResumeAsset | None
    draft_resume: ResumeAsset | None
    existing_email: RecruiterEmail | None = None
    external_thread_id: str | None = None
    routing_decision: RoutingDecision | None = None
    parsed_overrides: Mapping[str, str | int | bool] | None = None
    parser_details: Mapping[str, Any] | None = None
    precomputed_ai_score: float | None = None
    precomputed_ai_summary: str | None = None
    precomputed_ai_score_source: str | None = None
    precomputed_email_embedding_json: str | None = None
    precomputed_resume_embedding_json: str | None = None
    precomputed_semantic_diag: Any | None = None


@dataclass(frozen=True)
class QueuePreparationResult:
    outcome: str
    parsed: dict[str, str | int | bool]
    hard_filter_reason: str
    ai_score: float
    ai_summary: str
    ai_score_source: str
    email_embedding_json: str | None
    resume_embedding_json: str | None
    semantic_diag: Any
    routing_decision: RoutingDecision | None
    decision_reason: str
    auto_reject_reason: str | None
    skip_reason: str | None
    qualification_result: str | None
    blocking_rule: str | None
    qualification_detail: str | None
    qualification_context: dict[str, object] | None
    draft_reply: str | None
    draft_source: str | None
    draft_model: str | None
    draft_ai_error: str | None
    draft_resume_context_status: str | None


def describe_hard_filter_block(
    *,
    parsed: Mapping[str, str | int | bool],
    user_settings: UserSettings,
    effective_policy: Mapping[str, Any],
    hard_reason: str,
) -> tuple[str, str, dict[str, object]]:
    reasons = [part.strip() for part in hard_reason.removeprefix("blocked: ").split(",") if part.strip()]
    rules = policy_service.draft_rules(effective_policy)
    accepted_rule = rules["accepted_location"]
    accepted_from_rule = [loc.strip() for loc in accepted_rule.get("locations", []) if loc.strip()]
    accepted_locations = accepted_from_rule or [loc.strip() for loc in user_settings.accepted_locations.split(",") if loc.strip()]
    if "location_mismatch" in reasons:
        actual_location = str(parsed.get("location") or "unknown")
        return (
            "accepted_location",
            f'Location "{actual_location}" did not match accepted locations: {", ".join(accepted_locations) or "none"}.',
            {"actual_location": actual_location, "accepted_locations": accepted_locations},
        )
    if "salary_below_min" in reasons:
        minimum_salary = rules["minimum_salary"].get("value")
        if minimum_salary is None:
            minimum_salary = user_settings.min_salary
        actual_floor = _parse_salary_floor(str(parsed.get("salary_text") or ""))
        return (
            "minimum_salary",
            f'Salary floor {actual_floor if actual_floor is not None else "unknown"} was below minimum {minimum_salary}.',
            {"actual_salary_floor": actual_floor, "minimum_salary": minimum_salary},
        )
    missing_skills_reason = next((reason for reason in reasons if reason.startswith("missing_skills:")), None)
    if missing_skills_reason:
        missing_skills = [skill for skill in missing_skills_reason.split(":", 1)[1].split("|") if skill]
        return (
            "must_have_skills",
            f'Missing required skills: {", ".join(missing_skills)}.',
            {"missing_skills": missing_skills},
        )
    return (
        "hard_filters",
        "One or more qualification rules blocked this message.",
        {"reasons": reasons},
    )


def describe_score_threshold_block(*, ai_score: float, threshold: float) -> tuple[str, str, dict[str, object]]:
    return (
        "score_threshold",
        f"Resume-fit score {ai_score:.2f} was below threshold {threshold:.2f}.",
        {"ai_score": round(ai_score, 4), "threshold": round(threshold, 4)},
    )


def describe_f2f_block(*, block_reason: str) -> tuple[str, str, dict[str, object]]:
    return (
        "f2f_non_texas",
        block_reason or "Face-to-face requirement blocked this role.",
        {"policy_reason": block_reason},
    )


def describe_routing_block(routing_decision: RoutingDecision) -> tuple[str, str, dict[str, object]]:
    detail = routing_decision.reason or "Recipient routing could not resolve both recruiter To and employer CC."
    return (
        "recipient_mapping",
        detail,
        {
            "routing_status": routing_decision.status,
            "recommended_skip_reason": routing_decision.recommended_skip_reason,
        },
    )


def _valid_nvoids_listing_url(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return None
    host = (parsed.netloc or "").lower()
    if not host.endswith("nvoids.com"):
        return None
    path = (parsed.path or "").lower()
    if not path or path == "/":
        return None
    return raw


def prepend_nvoids_listing_line(draft_text: str | None, listing_url: str | None) -> str:
    draft = (draft_text or "").strip()
    valid_url = _valid_nvoids_listing_url(listing_url)
    if not valid_url:
        return draft
    prefix = f"Nvoids Listing: {valid_url}"
    if any(line.strip() == prefix for line in draft.splitlines()):
        return draft
    if not draft:
        return prefix
    return f"{prefix}\n\n{draft}"


def prepare_candidate_for_queue(
    request: QueuePreparationRequest,
    deps: QueuePreparationDependencies,
) -> QueuePreparationResult:
    if request.parsed_overrides:
        parsed = dict(request.parsed_overrides)
    else:
        parsed = deps.parse_email(request.subject, request.body)
    hard_pass, hard_reason = deps.hard_filter_check(
        parsed,
        request.user_settings,
        request.effective_policy,
        request.parser_details,
    )
    warning_messages: list[str] = []
    if hard_pass and hard_reason.startswith("warnings: "):
        warning_messages.extend([part.strip() for part in hard_reason.removeprefix("warnings: ").split(",") if part.strip()])
    if request.precomputed_ai_score is not None:
        ai_score = request.precomputed_ai_score
        ai_summary = request.precomputed_ai_summary or ""
        ai_score_source = request.precomputed_ai_score_source or "precomputed"
        email_embedding_json = request.precomputed_email_embedding_json
        resume_embedding_json = request.precomputed_resume_embedding_json
        semantic_diag = request.precomputed_semantic_diag
    else:
        ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json, semantic_diag = deps.compute_blended_ai_score(
            request.subject,
            request.body,
            parsed,
            request.user_settings,
            request.existing_email,
            request.scoring_resume,
            request.db,
            request.owner_id,
            str(request.external_thread_id or ""),
        )
    blocked, block_reason = deps.policy_f2f_block(parsed, request.effective_policy)
    if not blocked and block_reason:
        warning_messages.append(block_reason)
    score_mode = policy_service.draft_rule_mode(request.effective_policy, "score_threshold")
    score_blocked = score_mode == "block" and ai_score < request.threshold
    if score_mode == "warn" and ai_score < request.threshold:
        warning_messages.append(f"score_below_threshold:{ai_score:.2f}<{request.threshold:.2f}")
    if not hard_pass or score_blocked or blocked:
        if blocked:
            blocking_rule, qualification_detail, qualification_context = describe_f2f_block(block_reason=block_reason)
        elif not hard_pass:
            blocking_rule, qualification_detail, qualification_context = describe_hard_filter_block(
                parsed=parsed,
                user_settings=request.user_settings,
                effective_policy=request.effective_policy,
                hard_reason=hard_reason,
            )
        else:
            blocking_rule, qualification_detail, qualification_context = describe_score_threshold_block(
                ai_score=ai_score,
                threshold=request.threshold,
            )
        auto_reject_reason = "f2f_non_texas" if blocked else (hard_reason.removeprefix("blocked: ").strip() if not hard_pass else "ai_score_too_low")
        decision_reason = qualification_detail
        skip_reason = "f2f_non_texas_blocked" if blocked else "not_qualified"
        return QueuePreparationResult(
            outcome="not_qualified",
            parsed=parsed,
            hard_filter_reason=hard_reason,
            ai_score=ai_score,
            ai_summary=ai_summary,
            ai_score_source=ai_score_source,
            email_embedding_json=email_embedding_json,
            resume_embedding_json=resume_embedding_json,
            semantic_diag=semantic_diag,
            routing_decision=None,
            decision_reason=decision_reason,
            auto_reject_reason=auto_reject_reason,
            skip_reason=skip_reason,
            qualification_result="rejected",
            blocking_rule=blocking_rule,
            qualification_detail=qualification_detail,
            qualification_context=qualification_context,
            draft_reply=None,
            draft_source=None,
            draft_model=None,
            draft_ai_error=None,
            draft_resume_context_status=None,
        )

    routing_decision = request.routing_decision or deps.evaluate_routing_policy(
        request.db,
        request.sender,
        request.subject,
        request.body,
        request.snippet,
        bool(request.existing_email.routing_confirmed) if request.existing_email else False,
    )
    recipient_mapping_mode = policy_service.recipient_mapping_rule_mode(request.effective_policy)
    if routing_decision.should_mark_failed and recipient_mapping_mode == "block":
        blocking_rule, qualification_detail, qualification_context = describe_routing_block(routing_decision)
        return QueuePreparationResult(
            outcome="routing_failed",
            parsed=parsed,
            hard_filter_reason=hard_reason,
            ai_score=ai_score,
            ai_summary=ai_summary,
            ai_score_source=ai_score_source,
            email_embedding_json=email_embedding_json,
            resume_embedding_json=resume_embedding_json,
            semantic_diag=semantic_diag,
            routing_decision=routing_decision,
            decision_reason=qualification_detail,
            auto_reject_reason=None,
            skip_reason=routing_decision.recommended_skip_reason,
            qualification_result="rejected",
            blocking_rule=blocking_rule,
            qualification_detail=qualification_detail,
            qualification_context=qualification_context,
            draft_reply=None,
            draft_source=None,
            draft_model=None,
            draft_ai_error=None,
            draft_resume_context_status=None,
        )
    if routing_decision.should_mark_failed and recipient_mapping_mode == "warn":
        warning_messages.append(routing_decision.recommended_skip_reason or "missing_to_or_cc")

    greeting_line = deps.greeting_from_to_contact(routing_decision.to_email, request.body)
    fallback_reply = deps.build_user_fallback_draft(
        request.db,
        request.user_settings,
        request.sender,
        str(parsed["role"]),
        parsed,
        greeting_line,
        request.draft_resume.file_name if request.draft_resume else None,
    )
    if request.user_settings.feature_ai_enabled:
        if request.draft_resume:
            ai_reply = deps.generate_reply_with_ai_or_fallback(
                sender=request.sender,
                recruiter_to_email=routing_decision.to_email,
                greeting_line=greeting_line,
                subject=request.subject,
                body=request.body,
                role=str(parsed["role"]),
                location=str(parsed["location"]),
                salary_text=str(parsed["salary_text"]),
                skills_text=str(parsed["skills_text"]),
                resume_path=request.draft_resume.file_path,
                resume_file_name=request.draft_resume.file_name,
                fallback_draft=fallback_reply,
                model_name=request.model_name,
            )
            draft_reply = ai_reply.draft_text
            draft_source = ai_reply.source
            draft_model = ai_reply.ai_model
            draft_ai_error = ai_reply.ai_error
            draft_resume_context_status = ai_reply.resume_context_status
        else:
            draft_reply = fallback_reply
            draft_source = "rules_only"
            draft_model = None
            draft_ai_error = None
            draft_resume_context_status = RESUME_CONTEXT_MISSING
    else:
        draft_reply = fallback_reply
        draft_source = "rules_only"
        draft_model = None
        draft_ai_error = None
        draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY

    draft_reply = prepend_nvoids_listing_line(draft_reply, request.external_thread_id)
    hard_filter_reason = policy_service.combine_rule_messages(warning_messages)

    return QueuePreparationResult(
        outcome="needs_review",
        parsed=parsed,
        hard_filter_reason=hard_filter_reason,
        ai_score=ai_score,
        ai_summary=ai_summary,
        ai_score_source=ai_score_source,
        email_embedding_json=email_embedding_json,
        resume_embedding_json=resume_embedding_json,
        semantic_diag=semantic_diag,
        routing_decision=routing_decision,
        decision_reason="Qualified and queued for manual approval with warnings" if warning_messages else "Qualified and queued for manual approval",
        auto_reject_reason=None,
        skip_reason=None,
        qualification_result="qualified",
        blocking_rule=None,
        qualification_detail="Qualified for queue review.",
        qualification_context={"warnings": warning_messages},
        draft_reply=draft_reply,
        draft_source=draft_source,
        draft_model=draft_model,
        draft_ai_error=draft_ai_error,
        draft_resume_context_status=draft_resume_context_status,
    )
