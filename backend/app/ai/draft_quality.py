from __future__ import annotations

from dataclasses import asdict, dataclass


DraftQualityLabel = str
DraftGreetingCompliance = str


@dataclass(frozen=True)
class DraftQualityContract:
    content_valid: bool
    greeting_compliance: DraftGreetingCompliance
    resume_context_status: str
    confidence: float
    score: int
    label: DraftQualityLabel
    issues: list[str]

    def to_payload(self) -> dict[str, object]:
        return asdict(self)


def _clamp01(value: float | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _clamp100(value: float) -> int:
    return max(0, min(100, round(value)))


def _greeting_compliance(draft_text: str) -> DraftGreetingCompliance:
    lines = [line.strip() for line in draft_text.splitlines() if line.strip()]
    if not lines:
        return "missing"
    greeting_lines = [line for line in lines if line.lower().startswith("hi")]
    if not greeting_lines:
        return "missing"
    if len(greeting_lines) > 1:
        return "multiple"
    return "compliant"


def _score_label(score: int) -> DraftQualityLabel:
    if score >= 95:
        return "Excellent"
    if score >= 90:
        return "Strong"
    if score >= 80:
        return "Good"
    if score >= 70:
        return "Review"
    return "Risky"


def assess_draft_quality(
    *,
    draft_text: str,
    ai_score: float | None,
    routing_confidence: float | None,
    resume_context_status: str | None,
    recipient_email: str | None,
    cc_email: str | None,
    draft_ai_error: str | None,
) -> DraftQualityContract:
    issues: list[str] = []
    total = 50.0
    total += _clamp01(ai_score) * 30.0
    total += _clamp01(routing_confidence) * 20.0

    context_status = (resume_context_status or "unknown").strip() or "unknown"
    if context_status == "injected":
        total += 8
    elif context_status == "limited":
        total += 2
    elif context_status in {"missing_resume", "extract_failed"}:
        total -= 12
        issues.append("resume_context_incomplete")
    else:
        total -= 4
        issues.append("resume_context_unknown")

    greeting = _greeting_compliance(draft_text or "")
    if greeting == "missing":
        total -= 8
        issues.append("greeting_missing")
    elif greeting == "multiple":
        total -= 5
        issues.append("greeting_duplicated")

    if not recipient_email or not cc_email:
        total -= 6
        issues.append("recipient_mapping_incomplete")

    if not (draft_text or "").strip():
        total -= 10
        issues.append("draft_empty")

    if draft_ai_error:
        total -= 6
        issues.append("ai_fallback_error")

    score = _clamp100(total)
    label = _score_label(score)
    confidence = _clamp01((score / 100.0 + _clamp01(routing_confidence)) / 2.0)

    return DraftQualityContract(
        content_valid=bool((draft_text or "").strip()),
        greeting_compliance=greeting,
        resume_context_status=context_status,
        confidence=confidence,
        score=score,
        label=label,
        issues=issues,
    )
