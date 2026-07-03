from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from app.job_intent_learning import (
    JobIntentLearningSignal,
    NEGATIVE_CANDIDATE_HOTLIST,
    NEGATIVE_JOB_BOARD,
    NEGATIVE_NEWSLETTER,
    NEGATIVE_PLATFORM_NOTIFICATION,
    NEGATIVE_SECURITY,
    POSITIVE_RECRUITER_JD,
    normalize_job_intent_phrase,
)

JOB_STRUCTURE_TERMS = (
    "job description",
    "required skills",
    "requirements",
    "responsibilities",
    "must have",
    "position",
    "role",
    "job title",
    "title",
    "location",
    "duration",
    "rate",
    "contract",
    "onsite",
    "hybrid",
    "remote",
    "experience required",
    "required experience",
)

RECRUITER_ACTION_TERMS = (
    "share resume",
    "send resume",
    "submit resume",
    "let me know",
    "comfortable with the position",
    "work authorization",
    "visa status",
    "current location",
    "availability",
    "best rate",
    "need linkedin",
    "please check below job description",
    "please share your resume",
)

STAFFING_VENDOR_TERMS = (
    "c2c",
    "w2",
    "1099",
    "implementation partner",
    "client",
    "end client",
    "vendor",
    "local candidates",
    "only locals",
    "onsite interview",
    "interview mode",
)

CANDIDATE_MARKETING_TERMS = (
    "my candidate",
    "our candidate",
    "attached resume",
    "please find attached resume",
    "please share relevant requirements",
    "bench sales",
    "consultant details",
    "consultant summary",
    "available consultant",
    "candidate visa",
    "hotlist",
)

JOB_BOARD_ALERT_TERMS = (
    "job alert",
    "jobs alert",
    "recommended jobs",
    "new jobs alert",
    "job openings",
    "jobs for you",
    "recommended for you",
    "apply now",
)

PLATFORM_NOTIFICATION_TERMS = (
    "linkedin",
    "complete your profile",
    "profile completion",
    "profile views",
    "job recommendation",
    "jobs you may be interested in",
)

SECURITY_ALERT_TERMS = (
    "security alert",
    "google account",
    "password changed",
    "suspicious sign-in",
    "security code",
    "verify it was you",
)

NEWSLETTER_TERMS = (
    "newsletter",
    "weekly update",
    "monthly update",
    "digest",
)

WEAK_FOOTER_TERMS = (
    "unsubscribe",
    "googlegroups",
    "google groups",
    "you received this message because",
    "to unsubscribe from this group",
    "update:",
)

POSITIVE_ROLE_RE = re.compile(
    r"\b("
    r"developer|engineer|architect|analyst|administrator|consultant|scientist|"
    r"manager|lead|peoplesoft|react|node\.js|java|dot net|\.net|network engineer|"
    r"security|databricks|devops|frontend|backend|cloud"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobDescriptionTaxonomyDecision:
    intent_type: str
    action: str
    confidence: float
    reason: str
    evidence: list[str]
    negative_evidence: list[str]


def _normalize_text(*parts: str) -> str:
    joined = "\n".join(part.strip() for part in parts if part and part.strip())
    return re.sub(r"\s+", " ", joined).strip().lower()


def _collect_matches(text: str, terms: Sequence[str]) -> list[str]:
    found: list[str] = []
    for term in terms:
        if term in text and term not in found:
            found.append(term)
    return found


def _collect_learned_matches(
    text: str,
    signals: Sequence[JobIntentLearningSignal] | None,
    polarity: str,
) -> list[str]:
    if not signals:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for signal in signals:
        if signal.polarity != polarity:
            continue
        phrase = str(signal.phrase or "").strip()
        normalized = normalize_job_intent_phrase(phrase)
        if len(normalized) < 4 or normalized in seen:
            continue
        if normalized in text:
            seen.add(normalized)
            found.append(phrase)
    return found


def _footer_zone(body: str) -> str:
    raw = (body or "").strip().lower()
    if not raw:
        return ""
    marker_positions = [raw.find(marker) for marker in WEAK_FOOTER_TERMS if raw.find(marker) >= 0]
    if marker_positions:
        start = min(marker_positions)
        return _normalize_text(raw[start:])
    return _normalize_text(raw[-1200:])


def classify_job_description_taxonomy(
    *,
    sender: str,
    subject: str,
    body: str,
    snippet: str = "",
    recruiter_like: bool = False,
    approved_learning_signals: Sequence[JobIntentLearningSignal] | None = None,
) -> JobDescriptionTaxonomyDecision:
    header_text = _normalize_text(sender, subject, snippet)
    body_text = _normalize_text(body)
    footer_text = _footer_zone(body)
    full_text = _normalize_text(sender, subject, snippet, body)

    positive_evidence: list[str] = []
    negative_evidence: list[str] = []

    security_hits = _collect_matches(full_text, SECURITY_ALERT_TERMS) + _collect_learned_matches(
        full_text,
        approved_learning_signals,
        NEGATIVE_SECURITY,
    )
    if security_hits:
        return JobDescriptionTaxonomyDecision(
            intent_type="security_alert",
            action="skip",
            confidence=min(0.82 + 0.03 * len(security_hits), 0.98),
            reason="Matched account-security language rather than recruiter or job-description content.",
            evidence=[],
            negative_evidence=[f"security_alert:{item}" for item in security_hits],
        )

    structure_hits = _collect_matches(body_text, JOB_STRUCTURE_TERMS)
    recruiter_action_hits = _collect_matches(full_text, RECRUITER_ACTION_TERMS)
    staffing_hits = _collect_matches(full_text, STAFFING_VENDOR_TERMS)
    role_hits = sorted(set(match.group(0).lower() for match in POSITIVE_ROLE_RE.finditer(full_text)))
    learned_positive_hits = _collect_learned_matches(
        full_text,
        approved_learning_signals,
        POSITIVE_RECRUITER_JD,
    )
    weak_footer_hits = _collect_matches(footer_text, WEAK_FOOTER_TERMS)

    positive_evidence.extend(structure_hits)
    positive_evidence.extend(recruiter_action_hits)
    positive_evidence.extend(staffing_hits)
    positive_evidence.extend(f"learned_positive:{item}" for item in learned_positive_hits)
    if role_hits:
        positive_evidence.append(f"role_keyword:{role_hits[0]}")
    if recruiter_like:
        positive_evidence.append("recruiter_like_signal")

    positive_score = (
        len(structure_hits) * 2.4
        + len(recruiter_action_hits) * 2.0
        + len(staffing_hits) * 1.8
        + len(learned_positive_hits) * 2.2
        + (2.0 if role_hits else 0.0)
        + (0.8 if recruiter_like else 0.0)
    )

    candidate_hits = _collect_matches(full_text, CANDIDATE_MARKETING_TERMS) + _collect_learned_matches(
        full_text,
        approved_learning_signals,
        NEGATIVE_CANDIDATE_HOTLIST,
    )
    candidate_score = len(candidate_hits) * 2.4
    if candidate_score >= 3.6 and candidate_score >= positive_score + 0.8:
        return JobDescriptionTaxonomyDecision(
            intent_type="candidate_marketing_or_hotlist",
            action="skip",
            confidence=min(0.72 + 0.04 * len(candidate_hits), 0.97),
            reason="Matched consultant resume marketing or hotlist language rather than a recruiter-sent job requirement.",
            evidence=[],
            negative_evidence=[f"candidate_marketing:{item}" for item in candidate_hits],
        )

    job_board_hits = _collect_matches(header_text, JOB_BOARD_ALERT_TERMS) + _collect_learned_matches(
        full_text,
        approved_learning_signals,
        NEGATIVE_JOB_BOARD,
    )
    platform_hits = _collect_matches(full_text, PLATFORM_NOTIFICATION_TERMS) + _collect_learned_matches(
        full_text,
        approved_learning_signals,
        NEGATIVE_PLATFORM_NOTIFICATION,
    )
    if platform_hits and positive_score < 5.0:
        return JobDescriptionTaxonomyDecision(
            intent_type="linkedin_platform_notification",
            action="skip",
            confidence=min(0.74 + 0.04 * len(platform_hits), 0.97),
            reason="Matched platform-notification language rather than a recruiter-sent job requirement.",
            evidence=[],
            negative_evidence=[f"platform_notification:{item}" for item in platform_hits],
        )
    if job_board_hits and positive_score < 5.0:
        return JobDescriptionTaxonomyDecision(
            intent_type="job_board_alert",
            action="skip",
            confidence=min(0.74 + 0.04 * len(job_board_hits), 0.97),
            reason="Matched job-board recommendation or alert language rather than a direct recruiter requirement.",
            evidence=[],
            negative_evidence=[f"job_board:{item}" for item in job_board_hits],
        )

    strong_newsletter_hits = _collect_matches(header_text, NEWSLETTER_TERMS) + _collect_learned_matches(
        header_text,
        approved_learning_signals,
        NEGATIVE_NEWSLETTER,
    )
    if strong_newsletter_hits and positive_score < 3.0:
        return JobDescriptionTaxonomyDecision(
            intent_type="general_newsletter",
            action="skip",
            confidence=min(0.70 + 0.04 * len(strong_newsletter_hits), 0.95),
            reason="Matched newsletter-style language without enough recruiter job-description context.",
            evidence=[],
            negative_evidence=[f"newsletter:{item}" for item in strong_newsletter_hits],
        )

    if weak_footer_hits:
        negative_evidence.extend(f"weak_footer:{item}" for item in weak_footer_hits)

    if positive_score >= 4.0 and (structure_hits or recruiter_action_hits or staffing_hits or role_hits or learned_positive_hits):
        confidence = min(0.58 + (positive_score * 0.045), 0.96)
        reason = (
            "Matched recruiter job-description structure or submission language."
            if confidence >= 0.72
            else "Matched enough recruiter job-description context to continue, even with some weak footer/newsletter noise."
        )
        return JobDescriptionTaxonomyDecision(
            intent_type="recruiter_job_requirement",
            action="process_for_queue",
            confidence=confidence,
            reason=reason,
            evidence=positive_evidence,
            negative_evidence=negative_evidence,
        )

    if positive_score >= 2.2:
        return JobDescriptionTaxonomyDecision(
            intent_type="unknown",
            action="needs_review",
            confidence=min(0.50 + positive_score * 0.05, 0.76),
            reason="Found partial recruiter or job-structure evidence, but not enough for a confident fallback-only classification.",
            evidence=positive_evidence,
            negative_evidence=negative_evidence,
        )

    if weak_footer_hits:
        return JobDescriptionTaxonomyDecision(
            intent_type="unknown",
            action="needs_review",
            confidence=0.41,
            reason="Only weak footer/newsletter signals were found, so fallback mode is avoiding a hard skip.",
            evidence=positive_evidence,
            negative_evidence=negative_evidence,
        )

    negative_evidence.append("missing_contextual_job_signals")
    return JobDescriptionTaxonomyDecision(
        intent_type="unknown",
        action="needs_review",
        confidence=0.38,
        reason="Fallback taxonomy did not find enough contextual evidence to safely classify this Gmail candidate.",
        evidence=positive_evidence,
        negative_evidence=negative_evidence,
    )
