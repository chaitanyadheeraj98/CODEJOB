from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.phase0 import (
    EMAIL_RE,
    RoutingCandidates,
    RoutingEvidence,
    RoutingResult,
    extract_email_address,
    extract_recipient_routing_candidates,
)


@dataclass(frozen=True)
class RoutingPolicyInput:
    """Backward-compatible Gmail adapter input."""

    sender: str
    subject: str
    body: str
    snippet: str = ""
    learned_pairs: list[tuple[str, str]] | None = None
    employer_domains: list[str] | None = None
    routing_confirmed: bool = False
    preferred_employer_cc_emails: list[str] | None = None
    default_employer_cc_emails: list[str] | None = None
    preferred_employer_cc_email: str | None = None


@dataclass(frozen=True)
class CcSelectionRequest:
    to_candidates: list[RoutingEvidence]
    cc_candidates: list[RoutingEvidence]
    preferred_employer_cc_emails: list[str]
    default_employer_cc_emails: list[str]
    routing_confirmed: bool = False
    learned_pairs: list[tuple[str, str]] | None = None


@dataclass(frozen=True)
class RoutingDecision:
    to_email: str | None
    cc_email: str | None
    status: str
    confidence: float
    reason: str
    evidence: list[RoutingEvidence]
    candidates: list[RoutingEvidence]
    recommended_state: str
    recommended_skip_reason: str | None
    should_mark_failed: bool
    is_sendable_candidate: bool
    needs_manual_confirmation: bool

    def to_routing_result(self) -> RoutingResult:
        return RoutingResult(
            to_email=self.to_email,
            cc_email=self.cc_email,
            status=self.status,
            confidence=self.confidence,
            reason=self.reason,
            evidence=self.evidence,
            candidates=self.candidates,
        )


class RoutingAdapter(Protocol):
    def evaluate(self, payload: RoutingPolicyInput) -> RoutingCandidates | RoutingResult: ...


class HeuristicRoutingAdapter:
    def evaluate(self, payload: RoutingPolicyInput) -> RoutingCandidates:
        return extract_recipient_routing_candidates(
            payload.sender,
            payload.subject,
            payload.body,
            payload.snippet,
            learned_pairs=payload.learned_pairs or [],
            employer_domains=payload.employer_domains,
        )


class RoutingPolicyService:
    def __init__(self, adapter: RoutingAdapter | None = None) -> None:
        self._adapter = adapter or HeuristicRoutingAdapter()

    def evaluate(self, payload: CcSelectionRequest | RoutingPolicyInput) -> RoutingDecision:
        if isinstance(payload, RoutingPolicyInput):
            extracted = self._adapter.evaluate(payload)
            candidates = _candidate_set(extracted)
            preferred = payload.preferred_employer_cc_emails
            if preferred is None:
                legacy_preferred = _normalize_routing_email(payload.preferred_employer_cc_email)
                preferred = [legacy_preferred] if legacy_preferred else []
            request = CcSelectionRequest(
                to_candidates=candidates.to_candidates,
                cc_candidates=candidates.cc_candidates,
                preferred_employer_cc_emails=preferred,
                default_employer_cc_emails=payload.default_employer_cc_emails or [],
                routing_confirmed=payload.routing_confirmed,
                learned_pairs=payload.learned_pairs,
            )
        else:
            request = payload
        return _select_recipients(request)


def _select_recipients(request: CcSelectionRequest) -> RoutingDecision:
    to_candidates = _dedupe_evidence(request.to_candidates)
    sender_candidate = next((item for item in to_candidates if item.source == "sender_header"), None)
    other_to_candidates = [
        item
        for item in to_candidates
        if sender_candidate is None or item.email != sender_candidate.email
    ]

    ambiguous_to = len(other_to_candidates) > 1
    if ambiguous_to:
        selected_to = None
    elif other_to_candidates:
        selected_to = other_to_candidates[0]
    else:
        selected_to = sender_candidate

    candidate_cc = _dedupe_evidence(request.cc_candidates)
    preferred_cc = _configured_evidence(request.preferred_employer_cc_emails, "preferred_employer_cc")
    combined_cc = _dedupe_evidence([*candidate_cc, *preferred_cc])
    default_cc: list[RoutingEvidence] = []
    if not combined_cc:
        default_cc = _configured_evidence(request.default_employer_cc_emails, "default_employer_cc")
        combined_cc = _dedupe_evidence(default_cc)

    selected_to_email = selected_to.email if selected_to else None
    normalized_to = _normalize_routing_email(selected_to_email)
    selected_cc = [item for item in combined_cc if item.email != normalized_to][:3]
    selected_cc_emails = [item.email for item in selected_cc]
    cc_email = ", ".join(selected_cc_emails) or None

    has_pair = bool(selected_to_email and cc_email)
    learned_pairs = {
        (_normalize_routing_email(to_email), _normalize_routing_email(cc_address))
        for to_email, cc_address in request.learned_pairs or []
    }
    learned_match = bool(
        selected_to_email
        and any((_normalize_routing_email(selected_to_email), cc_address) in learned_pairs for cc_address in selected_cc_emails)
    )

    if has_pair:
        if learned_match:
            status = "confirmed"
            confidence = 0.92
            reason = "Matched a prior correction and resolved the recruiter To and employer CC recipients."
        else:
            status = "safe"
            confidence = 0.9
            reason = "Resolved the recruiter To and employer CC recipients."
    elif ambiguous_to:
        status = "ambiguous"
        confidence = 0.45
        reason = f"{len(other_to_candidates)} distinct recruiter contacts require manual selection."
    elif not cc_email:
        status = "missing"
        confidence = 0.0
        reason = "No usable employer CC remained. Default CC is missing in Execution Control."
    else:
        status = "missing"
        confidence = 0.0
        reason = "No usable recruiter To address was found."

    should_mark_failed = not has_pair
    is_sendable = has_pair and (
        request.routing_confirmed or (status in {"safe", "confirmed"} and confidence >= 0.8)
    )
    recommended_skip_reason = None
    if should_mark_failed:
        recommended_skip_reason = "missing_default_employer_cc" if not cc_email else "missing_to_or_cc"

    configured_candidates = [*preferred_cc, *default_cc]
    return RoutingDecision(
        to_email=selected_to_email,
        cc_email=cc_email,
        status=status,
        confidence=confidence,
        reason=reason,
        evidence=([selected_to] if selected_to else []) + selected_cc,
        candidates=[*request.to_candidates, *request.cc_candidates, *configured_candidates],
        recommended_state="failed" if should_mark_failed else "needs_review",
        recommended_skip_reason=recommended_skip_reason,
        should_mark_failed=should_mark_failed,
        is_sendable_candidate=is_sendable,
        needs_manual_confirmation=has_pair and not is_sendable,
    )


def _candidate_set(extracted: RoutingCandidates | RoutingResult) -> RoutingCandidates:
    if isinstance(extracted, RoutingCandidates):
        return extracted
    candidates = list(extracted.candidates or extracted.evidence)
    if extracted.to_email:
        candidates.insert(
            0,
            RoutingEvidence(role="to", email=extracted.to_email, source="precomputed", detail="Precomputed To"),
        )
    if extracted.cc_email:
        for email in extracted.cc_email.split(","):
            candidates.append(RoutingEvidence(role="cc", email=email, source="precomputed", detail="Precomputed CC"))
    return RoutingCandidates(
        to_candidates=[item for item in candidates if item.role == "to"],
        cc_candidates=[item for item in candidates if item.role == "cc"],
        candidates=candidates,
    )


def _configured_evidence(values: list[str], source: str) -> list[RoutingEvidence]:
    return [
        RoutingEvidence(role="cc", email=email, source=source, detail="Execution Control setting")
        for raw in values
        if (email := _normalize_routing_email(raw))
    ]


def _dedupe_evidence(items: list[RoutingEvidence]) -> list[RoutingEvidence]:
    unique: list[RoutingEvidence] = []
    seen: set[str] = set()
    for item in items:
        email = _normalize_routing_email(item.email)
        if not email or email in seen:
            continue
        seen.add(email)
        unique.append(RoutingEvidence(role=item.role, email=email, source=item.source, detail=item.detail))
    return unique


def _normalize_routing_email(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    email = extract_email_address(text).strip().lower()
    return email if EMAIL_RE.fullmatch(email) else ""
