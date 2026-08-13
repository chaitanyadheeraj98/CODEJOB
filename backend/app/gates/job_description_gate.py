from __future__ import annotations

from datetime import UTC, datetime
import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any, Sequence

from app.ai.groq_client import groq_chat_json, groq_request_mode_for_model
from app.config import settings
from app.job_intent_learning import (
    JobIntentLearningSignal,
    prepare_job_intent_model_text,
    prioritized_learning_signals,
)
from app.runtime_state import runtime_state
from app.taxonomy.job_description_taxonomy import (
    JobDescriptionTaxonomyDecision,
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


GROQ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "intent_type": {
            "type": "string",
            "enum": [
                "recruiter_job_requirement",
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
    "required": ["intent_type", "action", "confidence", "reason", "evidence", "negative_evidence"],
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


_SKIP_INTENT_TYPES = {
    "candidate_marketing_or_hotlist",
    "job_board_alert",
    "linkedin_platform_notification",
    "general_newsletter",
    "security_alert",
}


def _coerce_groq_payload(payload: dict[str, Any]) -> EmailIntentDecision | None:
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
        provider="groq",
        error=None,
        learned_signals=learned_signals,
    )


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

    started_at = datetime.now(UTC)
    runtime_state.groq_last_attempted_at = started_at
    request_mode = groq_request_mode_for_model(settings.groq_gate_model)
    runtime_state.groq_request_mode = request_mode
    positive_signals, negative_signals = prioritized_learning_signals(approved_learning_signals)
    body_for_model = prepare_job_intent_model_text(body)
    payload, error = groq_chat_json(
        system_prompt=(
            "Classify Gmail messages for job-intent gating. "
            "When Groq is enabled, you are the final intent authority. "
            "Do not require exact recruiter, staffing, or hiring words. "
            "Treat job-description structure, rate/location terms, visa/work authorization, "
            "C2C/W2/vendor/client language, implementation partner language, and resume-submission requests as strong positive evidence. "
            "Treat a trusted requirement group as positive source context, not as an automatic pass. "
            "Treat unsubscribe text, Google Groups footers, and reply prefixes as weak evidence only unless the rest of the email is clearly non-job. "
            "If a trusted group message is clearly a hotlist or candidate marketing, still classify it as candidate_marketing_or_hotlist. "
            "If the message is candidate marketing or a hotlist, classify it as candidate_marketing_or_hotlist instead of newsletter. "
            "If the body repeats the same set of candidate-profile fields (for example Full Legal Name, Current Location, Rate, "
            "Work Authorization) for two or more different people, this is a consultant hotlist being marketed to recruiters, not a job "
            "requirement being posted by one. Classify it as candidate_marketing_or_hotlist even when C2C/W2/visa/rate/resume-attached "
            "language is present -- repeated multi-candidate profile blocks always outweigh that positive evidence. "
            "Return 0-5 reusable learning_signals with concise phrases that would improve fallback classification later."
        ),
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
        strict=bool(settings.groq_gate_strict_json),
    )
    if payload is not None:
        decision = _coerce_groq_payload(payload)
        if decision is not None:
            disagreed = taxonomy.intent_type != decision.intent_type or taxonomy.action != decision.action
            if not disagreed:
                decision = replace(decision, learned_signals=[])
            duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            runtime_state.groq_last_success_at = datetime.now(UTC)
            runtime_state.groq_last_error = None
            runtime_state.groq_last_duration_ms = duration_ms
            runtime_state.groq_last_provider_result = "groq"
            logger.info(
                "Groq gate success model=%s mode=%s duration_ms=%s fallback_intent=%s groq_intent=%s disagreed=%s",
                settings.groq_gate_model or "llama-3.1-8b-instant",
                request_mode,
                duration_ms,
                taxonomy.intent_type,
                decision.intent_type,
                disagreed,
            )
            return decision
        error = "groq_invalid_shape"

    duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    runtime_state.groq_last_error = error
    runtime_state.groq_last_duration_ms = duration_ms
    runtime_state.groq_last_provider_result = "groq_fallback_taxonomy"
    logger.warning(
        "Groq gate failure error=%s model=%s mode=%s duration_ms=%s",
        error or "unknown",
        settings.groq_gate_model or "llama-3.1-8b-instant",
        request_mode,
        duration_ms,
    )
    return _taxonomy_to_decision(taxonomy, provider="groq_fallback_taxonomy", error=error)
