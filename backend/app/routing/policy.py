from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.phase0 import RoutingEvidence, RoutingResult, analyze_recipient_routing


@dataclass(frozen=True)
class RoutingPolicyInput:
    sender: str
    subject: str
    body: str
    snippet: str = ""
    learned_pairs: list[tuple[str, str]] | None = None
    routing_confirmed: bool = False


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
    def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult: ...


class HeuristicRoutingAdapter:
    def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
        return analyze_recipient_routing(
            payload.sender,
            payload.subject,
            payload.body,
            payload.snippet,
            learned_pairs=payload.learned_pairs or [],
        )


class LearnedRoutingAdapter:
    """Placeholder adapter for future learned routing models.

    Current behavior delegates to heuristic analysis for deterministic parity.
    """

    def __init__(self, fallback: RoutingAdapter | None = None) -> None:
        self._fallback = fallback or HeuristicRoutingAdapter()

    def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
        return self._fallback.evaluate(payload)


class RoutingPolicyService:
    def __init__(self, adapter: RoutingAdapter | None = None) -> None:
        self._adapter = adapter or HeuristicRoutingAdapter()

    def evaluate(self, payload: RoutingPolicyInput) -> RoutingDecision:
        routing = self._adapter.evaluate(payload)
        to_email = routing.to_email
        cc_email = routing.cc_email
        status = (routing.status or "").strip().lower()
        confidence = float(routing.confidence or 0.0)
        has_pair = bool(to_email and cc_email)
        is_sendable = payload.routing_confirmed or (status in {"safe", "confirmed"} and confidence >= 0.8)
        should_mark_failed = not has_pair
        recommended_state = "failed" if should_mark_failed else "needs_review"
        recommended_skip_reason = "missing_to_or_cc" if should_mark_failed else None
        needs_manual = has_pair and not is_sendable
        return RoutingDecision(
            to_email=to_email,
            cc_email=cc_email,
            status=routing.status,
            confidence=routing.confidence,
            reason=routing.reason,
            evidence=routing.evidence,
            candidates=routing.candidates,
            recommended_state=recommended_state,
            recommended_skip_reason=recommended_skip_reason,
            should_mark_failed=should_mark_failed,
            is_sendable_candidate=is_sendable,
            needs_manual_confirmation=needs_manual,
        )
