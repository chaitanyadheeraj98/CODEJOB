import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient

from app import main


class GmailLabelingApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)

    def test_preview_returns_rule_decision(self) -> None:
        response = self.client.post(
            "/gmail/labeling/preview",
            json={
                "sender": "Recruiter <r@example.com>",
                "subject": "Interview schedule",
                "body": "Please confirm your interview availability",
                "state": "needs_review",
                "decision": "Qualified",
                "routing_status": "safe",
                "routing_confidence": 0.9,
                "skip_reason": None,
                "draft_reply": "Thanks",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["label"], "Interview")
        self.assertTrue(payload["reason_path"].startswith("rules:"))

    def test_preview_uses_ai_fallback_when_rules_do_not_match(self) -> None:
        with patch("app.gmail_labeling.service.classify_label_with_ai", return_value="screening"):
            response = self.client.post(
                "/gmail/labeling/preview",
                json={
                    "sender": "no-signal@example.com",
                    "subject": "Hello",
                    "body": "Checking in",
                    "state": "needs_review",
                    "decision": "Qualified",
                    "routing_status": "unverified",
                    "routing_confidence": 0.0,
                    "skip_reason": None,
                    "draft_reply": "",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["label"], "screening")
        self.assertEqual(payload["reason_path"], "ai_fallback")


if __name__ == "__main__":
    unittest.main()
