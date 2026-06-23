from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from app.ai.resume_context_attribution import RESUME_CONTEXT_MISSING, RESUME_CONTEXT_RULES_ONLY
from app.models import RecruiterEmail, ResumeAsset, UserSettings
from app.routing import RoutingDecision


@dataclass(frozen=True)
class QueuePreparationDependencies:
    parse_email: Callable[[str, str], dict[str, str | int | bool]]
    hard_filter_check: Callable[[dict[str, str | int | bool], UserSettings], tuple[bool, str]]
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
    draft_reply: str | None
    draft_source: str | None
    draft_model: str | None
    draft_ai_error: str | None
    draft_resume_context_status: str | None


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
    hard_pass, hard_reason = deps.hard_filter_check(parsed, request.user_settings)
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
    if not hard_pass or ai_score < request.threshold or blocked:
        auto_reject_reason = "f2f_non_texas" if blocked else (hard_reason if not hard_pass else "ai_score_too_low")
        decision_reason = block_reason if blocked else "Not qualified for auto-reply"
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
    if routing_decision.should_mark_failed:
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
            decision_reason="Recipient routing unresolved",
            auto_reject_reason=None,
            skip_reason=routing_decision.recommended_skip_reason,
            draft_reply=None,
            draft_source=None,
            draft_model=None,
            draft_ai_error=None,
            draft_resume_context_status=None,
        )

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

    return QueuePreparationResult(
        outcome="needs_review",
        parsed=parsed,
        hard_filter_reason=hard_reason,
        ai_score=ai_score,
        ai_summary=ai_summary,
        ai_score_source=ai_score_source,
        email_embedding_json=email_embedding_json,
        resume_embedding_json=resume_embedding_json,
        semantic_diag=semantic_diag,
        routing_decision=routing_decision,
        decision_reason="Qualified and queued for manual approval",
        auto_reject_reason=None,
        skip_reason=None,
        draft_reply=draft_reply,
        draft_source=draft_source,
        draft_model=draft_model,
        draft_ai_error=draft_ai_error,
        draft_resume_context_status=draft_resume_context_status,
    )
