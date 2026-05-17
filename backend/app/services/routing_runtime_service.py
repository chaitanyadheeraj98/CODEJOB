from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.models import RecipientRoutingFeedback, RecruiterEmail
from app.phase0 import RoutingEvidence, RoutingResult, analyze_recipient_routing, email_domain
from app.routing import HeuristicRoutingAdapter, LearnedRoutingAdapter, RoutingDecision, RoutingPolicyInput, RoutingPolicyService


@dataclass
class RoutingRuntimeDeps:
    owner_id: str
    get_employer_domains: Callable[[Session], list[str]]


class RoutingRuntimeService:
    def __init__(self, deps: RoutingRuntimeDeps):
        self.deps = deps

    def learned_recipient_pairs(self, db: Session, sender: str) -> list[tuple[str, str]]:
        sender_domain = email_domain(sender)
        if not sender_domain:
            return []
        feedback_rows = (
            db.query(RecipientRoutingFeedback)
            .filter(
                RecipientRoutingFeedback.owner_id == self.deps.owner_id,
                RecipientRoutingFeedback.sender_domain == sender_domain,
            )
            .order_by(RecipientRoutingFeedback.id.desc())
            .limit(25)
            .all()
        )
        return [(row.corrected_to, row.corrected_cc) for row in feedback_rows]

    def routing_payload_json(self, items: list[RoutingEvidence]) -> str:
        return json.dumps([asdict(item) for item in items])

    def apply_routing_result(self, email: RecruiterEmail, routing: RoutingResult) -> None:
        email.recipient_email = routing.to_email
        email.cc_email = routing.cc_email
        email.routing_status = routing.status
        email.routing_confidence = routing.confidence
        email.routing_reason = routing.reason
        email.routing_evidence = self.routing_payload_json(routing.evidence)
        email.routing_candidates = self.routing_payload_json(routing.candidates)

    def apply_routing_decision(self, email: RecruiterEmail, routing: RoutingDecision) -> None:
        email.recipient_email = routing.to_email
        email.cc_email = routing.cc_email
        email.routing_status = routing.status
        email.routing_confidence = routing.confidence
        email.routing_reason = routing.reason
        email.routing_evidence = self.routing_payload_json(routing.evidence)
        email.routing_candidates = self.routing_payload_json(routing.candidates)

    def analyze_email_routing(self, db: Session, sender: str, subject: str, body: str, snippet: str = "") -> RoutingResult:
        return analyze_recipient_routing(
            sender,
            subject,
            body,
            snippet,
            learned_pairs=self.learned_recipient_pairs(db, sender),
            employer_domains=self.deps.get_employer_domains(db),
        )

    def evaluate_routing_policy(
        self,
        db: Session | None,
        sender: str,
        subject: str,
        body: str,
        snippet: str = "",
        routing_confirmed: bool = False,
        *,
        precomputed: RoutingResult | None = None,
    ) -> RoutingDecision:
        if precomputed is not None:
            class _PrecomputedAdapter:
                def __init__(self, result: RoutingResult) -> None:
                    self._result = result

                def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
                    _ = payload
                    return self._result

            service = RoutingPolicyService(adapter=_PrecomputedAdapter(precomputed))
            return service.evaluate(
                RoutingPolicyInput(
                    sender=sender,
                    subject=subject,
                    body=body,
                    snippet=snippet,
                    learned_pairs=[],
                    routing_confirmed=routing_confirmed,
                )
            )

        learned_pairs = self.learned_recipient_pairs(db, sender) if db else []
        employer_domains = self.deps.get_employer_domains(db) if db is not None else None
        adapter = LearnedRoutingAdapter(fallback=HeuristicRoutingAdapter())
        service = RoutingPolicyService(adapter=adapter)
        return service.evaluate(
            RoutingPolicyInput(
                sender=sender,
                subject=subject,
                body=body,
                snippet=snippet,
                learned_pairs=learned_pairs,
                employer_domains=employer_domains,
                routing_confirmed=routing_confirmed,
            )
        )

    def routing_is_sendable(self, email: RecruiterEmail) -> bool:
        decision = self.evaluate_routing_policy(
            None,
            email.sender,
            email.subject,
            email.body,
            "",
            email.routing_confirmed,
            precomputed=RoutingResult(
                to_email=email.recipient_email,
                cc_email=email.cc_email,
                status=email.routing_status,
                confidence=float(email.routing_confidence or 0.0),
                reason=email.routing_reason,
                evidence=[],
                candidates=[],
            ),
        )
        return decision.is_sendable_candidate
