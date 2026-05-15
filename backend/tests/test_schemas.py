import json
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.schemas import EmailResponse, SettingsRequest


class EmailResponseRoutingTests(unittest.TestCase):
    def test_routing_json_fields_parse_to_lists(self) -> None:
        now = datetime.now(UTC)
        source = SimpleNamespace(
            id=1,
            owner_id="default-owner",
            sender="Recruiter <recruiter@example.com>",
            subject="Java Developer",
            body="body",
            role="Java Developer",
            location="Texas",
            salary_text="not_specified",
            skills_text="java",
            score=80,
            decision="Qualified",
            state="needs_review",
            decision_reason=None,
            hard_filter_result=None,
            auto_reject_reason=None,
            ai_score=0.8,
            ai_score_source="test",
            ai_summary=None,
            skip_reason=None,
            sync_batch_id=None,
            draft_reply="draft",
            approval_status="pending",
            sent_status="not_sent",
            source="gmail",
            external_message_id="abc",
            external_thread_id="abc",
            external_rfc_message_id=None,
            gmail_message_url="https://mail.google.com/",
            recipient_email="recruiter@example.com",
            cc_email="employer@example.com",
            routing_status="safe",
            routing_confidence=0.9,
            routing_reason="Found distinct recruiter and employer contacts.",
            routing_evidence=json.dumps(
                [{"role": "to", "email": "recruiter@example.com", "source": "body", "detail": "Email body"}]
            ),
            routing_candidates="[]",
            routing_confirmed=False,
            resume_asset_id=None,
            resume_file_name=None,
            sent_at=None,
            gmail_sent_id=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )

        response = EmailResponse.model_validate(source)

        self.assertEqual(response.routing_evidence[0].email, "recruiter@example.com")
        self.assertEqual(response.routing_candidates, [])

    def test_settings_request_accepts_employer_domains(self) -> None:
        payload = SettingsRequest.model_validate({"employer_domains": ["horizonsofttech.net"]})
        self.assertEqual(payload.employer_domains, ["horizonsofttech.net"])


if __name__ == "__main__":
    unittest.main()
