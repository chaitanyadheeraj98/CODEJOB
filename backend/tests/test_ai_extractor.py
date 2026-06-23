import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.deepseek_client import deepseek_json_completion
from app.parsing.ai_extractor import ai_extractor_result_to_payload, extract_ai_job_details


def _fake_response(content: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class DeepSeekJsonCompletionTests(unittest.TestCase):
    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_parses_plain_json_object_response(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response('{"role":"Java Developer"}')

        payload = deepseek_json_completion("system", "user")

        self.assertEqual(payload["role"], "Java Developer")

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_extracts_json_from_fenced_response(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response(
            '```json\n{"company":"Acme Corp"}\n```'
        )

        payload = deepseek_json_completion("system", "user")

        self.assertEqual(payload["company"], "Acme Corp")


class AIExtractorTests(unittest.TestCase):
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        return_value={
            "role_candidates": ["Senior Java Engineer", "Java Engineer"],
            "company": "Acme Corp",
            "primary_location": "Dallas, TX",
            "mentioned_locations": ["Dallas, TX", "Remote"],
            "work_mode": "hybrid",
            "visa_hints": ["H1B"],
            "experience_years_min": 8,
            "skills_approved": ["Java", "Amazon ECS", "Grafana"],
            "skills_unknown": ["Temporal"],
            "confidence": 0.86,
            "evidence": {"role": ["Role: Senior Java Engineer"], "skills": ["Java, Amazon ECS, Grafana, Temporal"]},
        },
    )
    def test_returns_structured_result_with_known_and_unknown_skills(self, _mock_completion) -> None:
        result = extract_ai_job_details("Senior Java Engineer", "Role: Senior Java Engineer")
        payload = ai_extractor_result_to_payload(result)

        self.assertEqual(payload["role_candidates"], ["Senior Java Engineer", "Java Engineer"])
        self.assertEqual(payload["company"], "Acme Corp")
        self.assertEqual(payload["primary_location"], "Dallas, TX")
        self.assertEqual(payload["work_mode"], "hybrid")
        self.assertEqual(payload["visa_hints"], ["H1B"])
        self.assertEqual(payload["experience_years_min"], 8)
        self.assertEqual(payload["skills_approved"], ["Java", "Amazon ECS", "Grafana"])
        self.assertEqual(payload["skills_unknown"], ["Temporal"])
        self.assertEqual(payload["confidence"], 0.86)
        self.assertIn("role", payload["evidence"])

    @patch("app.parsing.ai_extractor.deepseek_json_completion", side_effect=RuntimeError("malformed response"))
    def test_handles_malformed_response_with_safe_noop(self, _mock_completion) -> None:
        result = extract_ai_job_details("Subject", "Body")
        payload = ai_extractor_result_to_payload(result)

        self.assertEqual(payload["role_candidates"], [])
        self.assertEqual(payload["skills_approved"], [])
        self.assertEqual(payload["skills_unknown"], [])
        self.assertEqual(payload["confidence"], 0.0)
        self.assertEqual(payload["error"], "malformed response")
        self.assertEqual(payload["evidence"]["extractor_error"], ["malformed response"])


if __name__ == "__main__":
    unittest.main()
