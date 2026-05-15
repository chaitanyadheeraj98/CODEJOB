from __future__ import annotations

from dataclasses import dataclass
import logging

from app.gmail_client import apply_gmail_label, ensure_gmail_labels
from app.gmail_labeling.classifier import classify_label_with_ai
from app.gmail_labeling.rules import ALLOWED_LABELS, LabelDecision, LabelRuleInput, choose_label_by_rules

logger = logging.getLogger(__name__)


@dataclass
class LabelStats:
    rules_hit: int = 0
    ai_fallback: int = 0
    apply_success: int = 0
    apply_failure: int = 0
    idempotent_skip: int = 0


class GmailLabelingService:
    def __init__(self) -> None:
        self._label_ids_by_display: dict[str, str] = {}
        self.stats = LabelStats()

    @staticmethod
    def _normalize(name: str) -> str:
        return (name or "").strip().lower()

    def ensure_target_labels(self) -> None:
        self._label_ids_by_display = ensure_gmail_labels(ALLOWED_LABELS)
        logger.info("gmail_labeling label_sync count=%s", len(self._label_ids_by_display))

    def resolve_label_id(self, label_name: str) -> str | None:
        if not self._label_ids_by_display:
            self.ensure_target_labels()
        normalized_target = self._normalize(label_name)
        for display_name, label_id in self._label_ids_by_display.items():
            if self._normalize(display_name) == normalized_target:
                return label_id
        return None

    def decide_label(self, rule_input: LabelRuleInput) -> LabelDecision:
        by_rules = choose_label_by_rules(rule_input)
        if by_rules:
            self.stats.rules_hit += 1
            return by_rules
        self.stats.ai_fallback += 1
        label = classify_label_with_ai(
            sender=rule_input.sender,
            subject=rule_input.subject,
            body=rule_input.body,
        )
        return LabelDecision(label=label, reason_path="ai_fallback")

    def apply_to_message(
        self,
        *,
        message_id: str,
        label_name: str,
        existing_label_ids: list[str] | None = None,
    ) -> tuple[bool, str | None]:
        label_id = self.resolve_label_id(label_name)
        if not label_id:
            self.stats.apply_failure += 1
            return False, None
        if existing_label_ids and label_id in existing_label_ids:
            self.stats.idempotent_skip += 1
            return False, label_id
        changed = apply_gmail_label(message_id, label_id, existing_label_ids=existing_label_ids)
        if changed:
            self.stats.apply_success += 1
        else:
            self.stats.idempotent_skip += 1
        return changed, label_id
