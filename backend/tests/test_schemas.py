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
            ats_score=84.5,
            ats_score_source="hybrid_structured_only",
            ats_summary="ATS hybrid score 84/100",
            ats_breakdown_json='{"raw_overlap":0.75,"selected_resume_file_name":"resume.docx"}',
            skip_reason=None,
            sync_batch_id=None,
            draft_reply="draft",
            approval_status="pending",
            sent_status="not_sent",
            source="gmail",
            external_message_id="abc",
            external_thread_id="abc",
            external_rfc_message_id=None,
            gmail_received_at=None,
            applied_gmail_label=None,
            applied_gmail_label_id=None,
            applied_gmail_label_at=None,
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
            parser_details_json='{"parser_version":"spacy_enrichment_v1","approved_skills_text":"java","unknown_skills":[],"merged_result":{"role":"Java Developer"}}',
            sent_at=None,
            gmail_sent_id=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )

        response = EmailResponse.model_validate(source)

        self.assertEqual(response.routing_evidence[0].email, "recruiter@example.com")
        self.assertEqual(response.routing_candidates, [])
        self.assertEqual(
            response.parser_details,
            {
                "parser_version": "spacy_enrichment_v1",
                "approved_skills_text": "java",
                "unknown_skills": [],
                "merged_result": {"role": "Java Developer"},
            },
        )
        self.assertEqual(response.ats_score, 84.5)
        self.assertEqual(
            response.ats_breakdown,
            {"raw_overlap": 0.75, "selected_resume_file_name": "resume.docx"},
        )

    def test_settings_request_accepts_employer_domains(self) -> None:
        payload = SettingsRequest.model_validate({"employer_domains": ["horizonsofttech.net"]})
        self.assertEqual(payload.employer_domains, ["horizonsofttech.net"])

    def test_settings_request_accepts_saved_gmail_queries(self) -> None:
        payload = SettingsRequest.model_validate({"saved_gmail_queries": ["is:unread", "tx is:unread"]})
        self.assertEqual(payload.saved_gmail_queries, ["is:unread", "tx is:unread"])

    def test_settings_request_accepts_ai_extractor_toggle(self) -> None:
        payload = SettingsRequest.model_validate({"feature_ai_extractor_enabled": True})
        self.assertTrue(payload.feature_ai_extractor_enabled)


if __name__ == "__main__":
    unittest.main()
