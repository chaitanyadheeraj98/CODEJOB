from __future__ import annotations

from dataclasses import dataclass


LABEL_ASSESSMENT = "assessment"
LABEL_AVAILABILITY_ACTION = "AVAILABILITY ACTION"
LABEL_INTERVIEW = "Interview"
LABEL_MUST_REPLY = "must reply"
LABEL_MUST_REPLY_IMPORTANT = "must reply/important"
LABEL_SCREENING = "screening"

ALLOWED_LABELS = [
    LABEL_ASSESSMENT,
    LABEL_AVAILABILITY_ACTION,
    LABEL_INTERVIEW,
    LABEL_MUST_REPLY,
    LABEL_MUST_REPLY_IMPORTANT,
    LABEL_SCREENING,
]


@dataclass(frozen=True)
class LabelRuleInput:
    sender: str
    subject: str
    body: str
    state: str
    decision: str
    routing_status: str
    routing_confidence: float
    skip_reason: str | None
    draft_reply: str


@dataclass(frozen=True)
class LabelDecision:
    label: str
    reason_path: str


def _contains_any(text: str, terms: list[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def choose_label_by_rules(payload: LabelRuleInput) -> LabelDecision | None:
    subject = payload.subject or ""
    body = payload.body or ""
    combined = f"{subject}\n{body}"
    sender_lower = (payload.sender or "").lower()

    if _contains_any(combined, ["interview", "schedule", "availability", "available for", "time slot"]):
        if _contains_any(combined, ["interview", "panel", "round", "technical interview"]):
            return LabelDecision(label=LABEL_INTERVIEW, reason_path="rules:interview_keywords")
        return LabelDecision(label=LABEL_AVAILABILITY_ACTION, reason_path="rules:availability_keywords")

    if _contains_any(combined, ["assessment", "coding test", "online test", "hackerrank", "codility", "take-home"]):
        return LabelDecision(label=LABEL_ASSESSMENT, reason_path="rules:assessment_keywords")

    if _contains_any(combined, ["urgent", "asap", "immediate", "today only"]):
        return LabelDecision(label=LABEL_MUST_REPLY_IMPORTANT, reason_path="rules:urgency_keywords")

    if payload.state == "needs_review" and payload.routing_status in {"safe", "confirmed"} and payload.routing_confidence >= 0.8:
        return LabelDecision(label=LABEL_MUST_REPLY, reason_path="rules:needs_review_safe_routing")

    if payload.state in {"processed_skipped", "auto_rejected", "failed"} or (payload.skip_reason or "").strip():
        return LabelDecision(label=LABEL_SCREENING, reason_path="rules:screening_by_state")

    if "@linkedin.com" in sender_lower and payload.decision != "Qualified":
        return LabelDecision(label=LABEL_SCREENING, reason_path="rules:linkedin_non_qualified")

    return None

