from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.models import JobIntentTaxonomyEntry

POSITIVE_RECRUITER_JD = "positive_recruiter_jd"
NEGATIVE_CANDIDATE_HOTLIST = "negative_candidate_hotlist"
NEGATIVE_JOB_BOARD = "negative_job_board"
NEGATIVE_PLATFORM_NOTIFICATION = "negative_platform_notification"
NEGATIVE_NEWSLETTER = "negative_newsletter"
NEGATIVE_SECURITY = "negative_security_alert"

VALID_JOB_INTENT_POLARITIES = {
    POSITIVE_RECRUITER_JD,
    NEGATIVE_CANDIDATE_HOTLIST,
    NEGATIVE_JOB_BOARD,
    NEGATIVE_PLATFORM_NOTIFICATION,
    NEGATIVE_NEWSLETTER,
    NEGATIVE_SECURITY,
}


@dataclass(frozen=True)
class JobIntentLearningSignal:
    phrase: str
    polarity: str
    confidence: float = 0.0
    id: int = 0


def prioritized_learning_signals(
    signals: Sequence[JobIntentLearningSignal] | None,
    *,
    limit: int = 15,
) -> tuple[list[JobIntentLearningSignal], list[JobIntentLearningSignal]]:
    ranked = sorted(
        signals or (),
        key=lambda item: (-float(item.confidence or 0.0), normalize_job_intent_phrase(item.phrase)),
    )
    positive = [item for item in ranked if item.polarity == POSITIVE_RECRUITER_JD][:limit]
    negative = [item for item in ranked if item.polarity != POSITIVE_RECRUITER_JD][:limit]
    return positive, negative


def prepare_job_intent_model_text(text: str) -> str:
    limit = max(500, int(settings.groq_gate_body_char_limit or 6000))
    prepared = (text or "").strip()[:limit]
    if not settings.groq_gate_redact_contact_info:
        return prepared
    prepared = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[email]", prepared)
    return re.sub(r"(?:\+?\d[\d(). -]{7,}\d)", "[phone]", prepared)


def normalize_job_intent_phrase(value: str | None) -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return re.sub(r"[^a-z0-9+.#/@:$% -]+", "", text).strip()


def _normalize_signal_list(items: Iterable[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        normalized = normalize_job_intent_phrase(raw)
        if len(normalized) < 4 or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(raw)
    return ordered


def default_polarity_for_intent(intent_type: str) -> str:
    normalized = (intent_type or "").strip().lower()
    if normalized == "recruiter_job_requirement":
        return POSITIVE_RECRUITER_JD
    if normalized == "candidate_marketing_or_hotlist":
        return NEGATIVE_CANDIDATE_HOTLIST
    if normalized == "job_board_alert":
        return NEGATIVE_JOB_BOARD
    if normalized == "linkedin_platform_notification":
        return NEGATIVE_PLATFORM_NOTIFICATION
    if normalized == "security_alert":
        return NEGATIVE_SECURITY
    return NEGATIVE_NEWSLETTER


def derive_learning_signals(
    *,
    intent_type: str,
    evidence: Sequence[str],
    negative_evidence: Sequence[str],
    learned_signals: Sequence[JobIntentLearningSignal] | None = None,
) -> list[JobIntentLearningSignal]:
    if learned_signals:
        normalized: list[JobIntentLearningSignal] = []
        seen: set[tuple[str, str]] = set()
        for signal in learned_signals:
            phrase = str(signal.phrase or "").strip()
            polarity = str(signal.polarity or "").strip().lower()
            normalized_phrase = normalize_job_intent_phrase(phrase)
            if not normalized_phrase or polarity not in VALID_JOB_INTENT_POLARITIES:
                continue
            key = (normalized_phrase, polarity)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(
                JobIntentLearningSignal(
                    phrase=phrase,
                    polarity=polarity,
                    confidence=max(0.0, min(float(signal.confidence or 0.0), 1.0)),
                )
            )
        if normalized:
            return normalized

    polarity = default_polarity_for_intent(intent_type)
    source = evidence if polarity == POSITIVE_RECRUITER_JD else (negative_evidence or evidence)
    return [
        JobIntentLearningSignal(phrase=item, polarity=polarity, confidence=0.6)
        for item in _normalize_signal_list(source)
    ]


def approved_learning_signals_for_owner(db: Session, owner_id: str) -> list[JobIntentLearningSignal]:
    rows = (
        db.query(JobIntentTaxonomyEntry)
        .filter(
            JobIntentTaxonomyEntry.owner_id == owner_id,
            JobIntentTaxonomyEntry.status == "approved",
        )
        .order_by(JobIntentTaxonomyEntry.polarity.asc(), JobIntentTaxonomyEntry.phrase.asc(), JobIntentTaxonomyEntry.id.asc())
        .all()
    )
    return [
        JobIntentLearningSignal(
            phrase=str(row.phrase or "").strip(),
            polarity=str(row.polarity or "").strip(),
            confidence=float(row.confidence_aggregate or 0.0),
            id=row.id,
        )
        for row in rows
        if str(row.phrase or "").strip() and str(row.polarity or "").strip() in VALID_JOB_INTENT_POLARITIES
    ]


def record_pending_job_intent_learning(
    db: Session,
    *,
    owner_id: str,
    intent_type: str,
    confidence: float,
    evidence: Sequence[str],
    negative_evidence: Sequence[str],
    learned_signals: Sequence[JobIntentLearningSignal] | None = None,
) -> None:
    signals = derive_learning_signals(
        intent_type=intent_type,
        evidence=evidence,
        negative_evidence=negative_evidence,
        learned_signals=learned_signals,
    )
    if not signals:
        return

    for signal in signals:
        normalized_phrase = normalize_job_intent_phrase(signal.phrase)
        if not normalized_phrase or signal.polarity not in VALID_JOB_INTENT_POLARITIES:
            continue
        existing = (
            db.query(JobIntentTaxonomyEntry)
            .filter(
                JobIntentTaxonomyEntry.owner_id == owner_id,
                JobIntentTaxonomyEntry.normalized_phrase == normalized_phrase,
                JobIntentTaxonomyEntry.polarity == signal.polarity,
            )
            .order_by(JobIntentTaxonomyEntry.id.asc())
            .first()
        )
        sample_seed = [signal.phrase, *evidence[:3], *negative_evidence[:3]]
        if existing:
            if existing.status == "dismissed":
                continue
            try:
                existing_samples = json.loads(existing.sample_evidence_json or "[]")
            except json.JSONDecodeError:
                existing_samples = []
            sample_values = _normalize_signal_list([*existing_samples, *sample_seed])[:6]
            prior_count = max(0, int(existing.source_examples_count or 0))
            new_count = prior_count + 1
            old_avg = float(existing.confidence_aggregate or 0.0)
            incoming = max(0.0, min(float(signal.confidence or confidence or 0.0), 1.0))
            existing.phrase = str(signal.phrase or existing.phrase).strip() or existing.phrase
            existing.source_examples_count = new_count
            existing.confidence_aggregate = ((old_avg * prior_count) + incoming) / new_count if new_count else incoming
            existing.sample_evidence_json = json.dumps(sample_values, separators=(",", ":"))
            existing.last_intent_type = intent_type
            continue
        incoming = max(0.0, min(float(signal.confidence or confidence or 0.0), 1.0))
        db.add(
            JobIntentTaxonomyEntry(
                owner_id=owner_id,
                phrase=str(signal.phrase).strip(),
                normalized_phrase=normalized_phrase,
                polarity=signal.polarity,
                source_examples_count=1,
                sample_evidence_json=json.dumps(_normalize_signal_list(sample_seed)[:6], separators=(",", ":")),
                confidence_aggregate=incoming,
                last_intent_type=intent_type,
                status="pending",
            )
        )
