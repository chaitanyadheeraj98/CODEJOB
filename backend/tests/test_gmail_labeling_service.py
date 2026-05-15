import unittest
from unittest.mock import patch

from app.gmail_labeling.rules import LABEL_SCREENING, LabelRuleInput
from app.gmail_labeling.service import GmailLabelingService


class GmailLabelingServiceTests(unittest.TestCase):
    def test_resolve_label_id_case_insensitive(self) -> None:
        service = GmailLabelingService()
        with patch("app.gmail_labeling.service.ensure_gmail_labels", return_value={"Interview": "Label_1"}):
            service.ensure_target_labels()
        self.assertEqual(service.resolve_label_id("interview"), "Label_1")
        self.assertEqual(service.resolve_label_id("Interview"), "Label_1")

    def test_ai_fallback_used_when_no_rule_matches(self) -> None:
        service = GmailLabelingService()
        payload = LabelRuleInput(
            sender="foo@example.com",
            subject="hello",
            body="just checking in",
            state="needs_review",
            decision="Qualified",
            routing_status="unverified",
            routing_confidence=0.0,
            skip_reason=None,
            draft_reply="",
        )
        with patch("app.gmail_labeling.service.classify_label_with_ai", return_value=LABEL_SCREENING):
            decision = service.decide_label(payload)
        self.assertEqual(decision.label, LABEL_SCREENING)
        self.assertEqual(decision.reason_path, "ai_fallback")

    def test_apply_is_idempotent_when_label_already_present(self) -> None:
        service = GmailLabelingService()
        with patch("app.gmail_labeling.service.ensure_gmail_labels", return_value={"screening": "Label_2"}):
            service.ensure_target_labels()
        with patch("app.gmail_labeling.service.apply_gmail_label") as apply_fn:
            changed, label_id = service.apply_to_message(
                message_id="m-1",
                label_name="screening",
                existing_label_ids=["Label_2"],
            )
        self.assertFalse(changed)
        self.assertEqual(label_id, "Label_2")
        apply_fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

