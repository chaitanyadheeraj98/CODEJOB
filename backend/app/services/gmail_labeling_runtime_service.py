from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import cast

from app.gmail_client import GmailMessageCandidate
from app.gmail_labeling import GmailLabelingService, LabelRuleInput
from app.models import RecruiterEmail
from app.runtime_state import runtime_state

logger = logging.getLogger(__name__)


class GmailLabelingRuntimeService:
    def ensure_service(self) -> GmailLabelingService:
        if runtime_state.gmail_labeling_service is None:
            runtime_state.gmail_labeling_service = GmailLabelingService()
        return runtime_state.gmail_labeling_service

    @staticmethod
    def build_label_rule_input(email: RecruiterEmail) -> LabelRuleInput:
        return LabelRuleInput(
            sender=email.sender or "",
            subject=email.subject or "",
            body=email.body or "",
            state=email.state or "",
            decision=email.decision or "",
            routing_status=email.routing_status or "",
            routing_confidence=float(email.routing_confidence or 0.0),
            skip_reason=email.skip_reason,
            draft_reply=email.draft_reply or "",
        )

    def apply_for_email(self, *, email: RecruiterEmail, candidate_item: GmailMessageCandidate | dict[str, object]) -> None:
        if email.source != "gmail" or not email.external_message_id:
            return
        service = self.ensure_service()
        decision = service.decide_label(self.build_label_rule_input(email))
        try:
            changed, label_id = service.apply_to_message(
                message_id=email.external_message_id,
                label_name=decision.label,
                existing_label_ids=cast(list[str], candidate_item.get("label_ids", [])),
            )
            email.applied_gmail_label = decision.label
            email.applied_gmail_label_id = label_id
            if changed:
                email.applied_gmail_label_at = datetime.now(UTC)
            logger.info(
                "gmail_labeling decision=%s path=%s changed=%s message_id=%s",
                decision.label,
                decision.reason_path,
                changed,
                email.external_message_id,
            )
        except Exception as exc:
            logger.warning("gmail_labeling apply_failed message_id=%s error=%s", email.external_message_id, exc)

    def log_stats(self) -> None:
        service = runtime_state.gmail_labeling_service
        if not service:
            return
        stats = service.stats
        logger.info(
            "gmail_labeling stats rules_hit=%s ai_fallback=%s apply_success=%s apply_failure=%s idempotent_skip=%s",
            stats.rules_hit,
            stats.ai_fallback,
            stats.apply_success,
            stats.apply_failure,
            stats.idempotent_skip,
        )
