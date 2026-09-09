from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from app.ai.groq_client import groq_request_mode_for_model
from app.ai.intent_provider import IntentUsage, intent_chat_json
from app.config import settings
from app.job_intent_learning import (
    JobIntentLearningSignal,
    prepare_job_intent_model_text,
    prioritized_learning_signals,
)
from app.runtime_state import runtime_state
from app.taxonomy.job_description_taxonomy import (
    JobDescriptionTaxonomyDecision,
    _count_candidate_profile_blocks,
    classify_job_description_taxonomy,
)
from app.services.gmail_group_source_service import TrustedGroupContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailIntentDecision:
    intent_type: str
    action: str
    confidence: float
    reason: str
    evidence: list[str]
    negative_evidence: list[str]
    provider: str
    error: str | None = None
    learned_signals: list[JobIntentLearningSignal] = field(default_factory=list)


# Providers whose verdict came from a model rather than the rules taxonomy. Callers
# gate learning-signal capture on this: a taxonomy fallback has no signals to learn
# from, and hardcoding `== "groq"` is exactly what breaks the day a second provider
# ships.
LLM_DECIDED_PROVIDERS = frozenset({"groq", "deepseek"})


def llm_decided(provider: str | None) -> bool:
    return (provider or "") in LLM_DECIDED_PROVIDERS


GROQ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent_type": {
            "type": "string",
            "enum": [
                "recruiter_job_requirement",
                "application_link_only",
                "candidate_marketing_or_hotlist",
                "job_board_alert",
                "linkedin_platform_notification",
                "general_newsletter",
                "security_alert",
                "unknown",
            ],
        },
        "action": {
            "type": "string",
            "enum": ["process_for_queue", "needs_review", "skip"],
        },
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "negative_evidence": {"type": "array", "items": {"type": "string"}},
        "learning_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "phrase": {"type": "string"},
                    "polarity": {
                        "type": "string",
                        "enum": [
                            "positive_recruiter_jd",
                            "negative_candidate_hotlist",
                            "negative_job_board",
                            "negative_platform_notification",
                            "negative_newsletter",
                            "negative_security_alert",
                        ],
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["phrase", "polarity", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence", "learning_signals"],
    "additionalProperties": False,
}


def _prompt_signal_phrases(signals: Sequence[JobIntentLearningSignal]) -> str:
    phrases = [" ".join(str(item.phrase or "").split())[:160] for item in signals]
    return json.dumps([phrase for phrase in phrases if phrase], ensure_ascii=True)


def _taxonomy_to_decision(
    result: JobDescriptionTaxonomyDecision,
    *,
    provider: str,
    error: str | None = None,
) -> EmailIntentDecision:
    return EmailIntentDecision(
        intent_type=result.intent_type,
        action=result.action,
        confidence=result.confidence,
        reason=result.reason,
        evidence=list(result.evidence),
        negative_evidence=list(result.negative_evidence),
        provider=provider,
        error=error,
        learned_signals=[],
    )


_PROFILE_BLOCK_CLAIM_RE = re.compile(r"repeated.{0,40}profile block|profile block.{0,40}repeated", re.IGNORECASE)

_SKIP_INTENT_TYPES = {
    "candidate_marketing_or_hotlist",
    "job_board_alert",
    "linkedin_platform_notification",
    "general_newsletter",
    "security_alert",
}


def _coerce_groq_payload(payload: dict[str, Any], *, provider: str = "groq") -> EmailIntentDecision | None:
    intent_type = str(payload.get("intent_type") or "").strip()
    action = str(payload.get("action") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    if not intent_type or not action or not reason:
        return None
    if intent_type in _SKIP_INTENT_TYPES:
        action = "skip"
    evidence = [str(item).strip() for item in payload.get("evidence", []) if str(item).strip()]
    negative_evidence = [str(item).strip() for item in payload.get("negative_evidence", []) if str(item).strip()]
    learned_signals: list[JobIntentLearningSignal] = []
    raw_learning_signals = payload.get("learning_signals", [])
    if isinstance(raw_learning_signals, list):
        for item in raw_learning_signals:
            if not isinstance(item, dict):
                continue
            phrase = str(item.get("phrase") or "").strip()
            polarity = str(item.get("polarity") or "").strip()
            if not phrase or not polarity:
                continue
            try:
                signal_confidence = max(0.0, min(float(item.get("confidence", 0.0)), 1.0))
            except (TypeError, ValueError):
                signal_confidence = 0.0
            learned_signals.append(
                JobIntentLearningSignal(
                    phrase=phrase,
                    polarity=polarity,
                    confidence=signal_confidence,
                )
            )
    try:
        confidence = max(0.0, min(float(payload.get("confidence", 0.0)), 1.0))
    except (TypeError, ValueError):
        return None
    return EmailIntentDecision(
        intent_type=intent_type,
        action=action,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        negative_evidence=negative_evidence,
        provider=provider,
        error=None,
        learned_signals=learned_signals,
    )


# Hoisted to module scope on purpose. DeepSeek's prompt cache keys on the literal
# prefix, and a cache hit is far cheaper than a miss on that segment. Interpolating
# anything per-email here - a sender, a date, a taxonomy hint - silently destroys
# that for every email, with nothing in the logs to show it. Per-email context
# belongs in the user prompt below. `test_system_prompt_is_invariant_across_emails`
# guards this.
GATE_SYSTEM_PROMPT = (
    "Classify Gmail messages for job-intent gating. "
    "When the intent gate is enabled, you are the final intent authority. "
    "Do not require exact recruiter, staffing, or hiring words. "
    "Treat job-description structure, rate/location terms, visa/work authorization, "
    "C2C/W2/vendor/client language, implementation partner language, and resume-submission requests as strong positive evidence. "
    "That positive evidence assumes the terms describe the role. If instead they are blank field prompts the candidate is "
    "asked to fill in (for example 'Visa Status:', 'Rate per Hour:', 'Current Location:' with nothing after the colon) and the "
    "email's only actual content is a link to an external application portal with no responsibilities, skills, or scope "
    "described in the body itself, classify it as application_link_only instead of recruiter_job_requirement -- a request "
    "for the candidate's own information is not a description of the job. If the body also describes real requirements "
    "(skills, responsibilities, technology, scope) alongside the link and fields, it is still recruiter_job_requirement. "
    "Treat a trusted requirement group as positive source context, not as an automatic pass. "
    "Treat unsubscribe text, Google Groups footers, and reply prefixes as weak evidence only unless the rest of the email is clearly non-job. "
    "If a trusted group message is clearly a hotlist or candidate marketing, still classify it as candidate_marketing_or_hotlist. "
    "If the message is candidate marketing or a hotlist, classify it as candidate_marketing_or_hotlist instead of newsletter. "
    "If the body repeats the same set of candidate-profile fields (for example Full Legal Name, Current Location, Rate, "
    "Work Authorization) for two or more different people, this is a consultant hotlist being marketed to recruiters, not a job "
    "requirement being posted by one. Classify it as candidate_marketing_or_hotlist even when C2C/W2/visa/rate/resume-attached "
    "language is present -- repeated multi-candidate profile blocks always outweigh that positive evidence. "
    "Return 0-5 reusable learning_signals with concise phrases that would improve fallback classification later."
)


def _fallback_provider_label(provider: str) -> str:
    """`taxonomy` is not "a provider that failed" - it is the configured answer."""
    return "taxonomy" if provider == "taxonomy" else f"{provider}_fallback_taxonomy"


def _record_gate_telemetry(
    *,
    provider: str,
    result: str,
    error: str | None,
    duration_ms: int,
    started_at: datetime,
    usage: IntentUsage | None,
) -> None:
    """Provider-neutral runtime telemetry, plus the legacy Groq fields.

    The `groq_last_*` fields are only touched when Groq actually ran. Writing
    DeepSeek's health into a field the AI Access card labels "Groq Runtime" would
    make the status surface lie, which is worse than showing "Unknown".
    """
    runtime_state.intent_gate_provider = provider
    runtime_state.intent_gate_last_attempted_at = started_at
    runtime_state.intent_gate_last_error = error
    runtime_state.intent_gate_last_duration_ms = duration_ms
    runtime_state.intent_gate_last_provider_result = result
    runtime_state.intent_gate_last_rung = (usage.rung if usage else "") or ""
    runtime_state.intent_gate_last_escalated = bool(usage.escalated) if usage else False
    if error is None:
        runtime_state.intent_gate_last_success_at = datetime.now(UTC)

    if provider != "groq":
        return
    runtime_state.groq_last_attempted_at = started_at
    runtime_state.groq_last_error = error
    runtime_state.groq_last_duration_ms = duration_ms
    runtime_state.groq_last_provider_result = result
    if error is None:
        runtime_state.groq_last_success_at = datetime.now(UTC)


def classify_email_intent(
    *,
    sender: str,
    subject: str,
    body: str,
    snippet: str = "",
    recruiter_like: bool = False,
    groq_enabled: bool = False,
    trusted_group_context: TrustedGroupContext | None = None,
    approved_learning_signals: Sequence[JobIntentLearningSignal] | None = None,
) -> EmailIntentDecision:
    taxonomy = classify_job_description_taxonomy(
        sender=sender,
        subject=subject,
        body=body,
        snippet=snippet,
        recruiter_like=recruiter_like,
        trusted_group_context=trusted_group_context,
        approved_learning_signals=approved_learning_signals,
    )

    if not groq_enabled:
        return _taxonomy_to_decision(taxonomy, provider="taxonomy")

    # Skip the model when the rules are already certain. Guarded on `> 0.0` so the
    # shipped default (0.0) never short-circuits: this is enabled only once the
    # agreement harness produces a defensible threshold. The distinct provider label
    # keeps skipped emails countable, rather than indistinguishable from "gate off".
    min_taxonomy_confidence = float(settings.intent_gate_min_taxonomy_confidence or 0.0)
    if min_taxonomy_confidence > 0.0 and taxonomy.confidence >= min_taxonomy_confidence:
        return _taxonomy_to_decision(taxonomy, provider="taxonomy_confident")

    provider = settings.intent_gate_provider
    started_at = datetime.now(UTC)
    if provider == "groq":
        runtime_state.groq_request_mode = groq_request_mode_for_model(settings.groq_gate_model)
    positive_signals, negative_signals = prioritized_learning_signals(approved_learning_signals)
    # Redaction and truncation happen HERE, above the provider seam, so every
    # provider inherits them. Never move a provider call above this line.
    body_for_model = prepare_job_intent_model_text(body)

    def agrees_with_taxonomy(candidate_payload: dict[str, Any]) -> bool:
        candidate = _coerce_groq_payload(candidate_payload, provider=provider)
        if candidate is None:
            return False
        return candidate.intent_type == taxonomy.intent_type and candidate.action == taxonomy.action

    payload, error, usage = intent_chat_json(
        system_prompt=GATE_SYSTEM_PROMPT,
        user_prompt=(
            f"Sender: {sender}\n"
            f"Subject: {subject}\n"
            f"Snippet: {snippet}\n"
            f"Recruiter-like signal: {recruiter_like}\n"
            f"Trusted group matched: {bool(trusted_group_context and trusted_group_context.matched)}\n"
            f"Trusted group name: {(trusted_group_context.group_name if trusted_group_context else '') or 'none'}\n"
            f"Trusted group email: {(trusted_group_context.group_email if trusted_group_context else '') or 'none'}\n"
            f"Trusted group match method: {(trusted_group_context.match_method if trusted_group_context else '') or 'none'}\n"
            f"Fallback taxonomy intent: {taxonomy.intent_type}\n"
            f"Fallback taxonomy action: {taxonomy.action}\n"
            f"Fallback taxonomy confidence: {taxonomy.confidence:.2f}\n"
            f"Fallback positive evidence: {', '.join(taxonomy.evidence) or 'none'}\n"
            f"Fallback negative evidence: {', '.join(taxonomy.negative_evidence) or 'none'}\n"
            f"Known confirmed positive signals for this inbox: {_prompt_signal_phrases(positive_signals)}\n"
            f"Known confirmed negative signals for this inbox: {_prompt_signal_phrases(negative_signals)}\n"
            "Use confirmed signals as supporting context, not as the sole basis for a decision.\n"
            f"Body:\n{body_for_model}\n"
        ),
        schema=GROQ_SCHEMA,
        agrees_with_taxonomy=agrees_with_taxonomy,
    )
    if payload is not None:
        decision = _coerce_groq_payload(payload, provider=provider)
        if decision is not None:
            if (
                decision.action == "skip"
                and _PROFILE_BLOCK_CLAIM_RE.search(decision.reason)
                and _count_candidate_profile_blocks(body) < 2
            ):
                # ponytail: the LLM's own stated reason claims repeated candidate-profile
                # blocks -- that specific claim is mechanically checkable, so verify it
                # rather than trusting whichever skip-intent label the LLM attached to it.
                decision = replace(decision, intent_type=taxonomy.intent_type, action=taxonomy.action)
            disagreed = taxonomy.intent_type != decision.intent_type or taxonomy.action != decision.action
            if not disagreed:
                decision = replace(decision, learned_signals=[])
            duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            _record_gate_telemetry(
                provider=provider,
                result=provider,
                error=None,
                duration_ms=duration_ms,
                started_at=started_at,
                usage=usage,
            )
            logger.info(
                "Intent gate success provider=%s model=%s rung=%s duration_ms=%s "
                "cache_hit_tokens=%s cache_miss_tokens=%s fallback_intent=%s model_intent=%s disagreed=%s",
                provider,
                (usage.model if usage else "") or "unknown",
                (usage.rung if usage else "") or "n/a",
                duration_ms,
                usage.prompt_cache_hit_tokens if usage else None,
                usage.prompt_cache_miss_tokens if usage else None,
                taxonomy.intent_type,
                decision.intent_type,
                disagreed,
            )
            return decision
        error = f"{provider}_invalid_shape"

    duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    fallback_provider = _fallback_provider_label(provider)
    _record_gate_telemetry(
        provider=provider,
        result=fallback_provider,
        error=error,
        duration_ms=duration_ms,
        started_at=started_at,
        usage=usage,
    )
    logger.warning(
        "Intent gate failure provider=%s error=%s rung=%s attempts=%s duration_ms=%s",
        provider,
        error or "unknown",
        (usage.rung if usage else "") or "n/a",
        usage.attempts if usage else 0,
        duration_ms,
    )
    return _taxonomy_to_decision(taxonomy, provider=fallback_provider, error=error)
