import unittest

from app.gmail_labeling.rules import (
    LABEL_AVAILABILITY_ACTION,
    LABEL_INTERVIEW,
    LABEL_MUST_REPLY_IMPORTANT,
    LABEL_SCREENING,
    LabelRuleInput,
    choose_label_by_rules,
)


class GmailLabelingRulesTests(unittest.TestCase):
    def _base(self) -> LabelRuleInput:
        return LabelRuleInput(
            sender="Recruiter <r@example.com>",
            subject="Role update",
            body="General follow up",
            state="needs_review",
            decision="Qualified",
            routing_status="unverified",
            routing_confidence=0.0,
            skip_reason=None,
            draft_reply="",
        )

    def test_interview_keyword_priority(self) -> None:
        payload = self._base()
        payload = LabelRuleInput(**{**payload.__dict__, "subject": "Interview Schedule for Java role"})
        result = choose_label_by_rules(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.label, LABEL_INTERVIEW)

    def test_availability_keyword_maps_to_availability_action(self) -> None:
        payload = self._base()
        payload = LabelRuleInput(**{**payload.__dict__, "body": "Please share your availability for next week."})
        result = choose_label_by_rules(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.label, LABEL_AVAILABILITY_ACTION)

    def test_urgent_maps_to_must_reply_important(self) -> None:
        payload = self._base()
        payload = LabelRuleInput(**{**payload.__dict__, "body": "Urgent requirement, please respond ASAP."})
        result = choose_label_by_rules(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.label, LABEL_MUST_REPLY_IMPORTANT)

    def test_failed_or_skipped_maps_to_screening(self) -> None:
        payload = self._base()
        payload = LabelRuleInput(**{**payload.__dict__, "state": "processed_skipped", "skip_reason": "not_qualified"})
        result = choose_label_by_rules(payload)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.label, LABEL_SCREENING)


if __name__ == "__main__":
    unittest.main()

