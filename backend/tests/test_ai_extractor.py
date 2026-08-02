import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ai.deepseek_client import DeepSeekJSONError, _parse_json_object, deepseek_json_completion
from app.parsing.ai_extractor import AI_EXTRACTOR_MAX_TOKENS, ai_extractor_result_to_payload, extract_ai_job_details
from ai_extractor_fixtures import (
    REAL_NVOIDS_LLMOPS_CHILD_BODY,
    REAL_NVOIDS_LLMOPS_CHILD_SOURCE,
    REAL_NVOIDS_LLMOPS_CHILD_SUBJECT,
)


def _fake_response(
    content: str,
    *,
    finish_reason: str = "stop",
    model: str = "deepseek-v4-flash",
    prompt_tokens: int = 101,
    completion_tokens: int = 23,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
        model=model,
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class DeepSeekJsonCompletionTests(unittest.TestCase):
    def test_parser_accepts_prose_wrapped_object_and_rejects_non_objects(self) -> None:
        self.assertEqual(_parse_json_object('Result: {"role":"Platform Engineer"} done.'), {"role": "Platform Engineer"})
        with self.assertRaisesRegex(RuntimeError, "malformed JSON content"):
            _parse_json_object('["not", "an", "object"]')
        with self.assertRaisesRegex(RuntimeError, "empty content"):
            _parse_json_object("")

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_parses_plain_json_object_response(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response('{"role":"Java Developer"}')

        payload = deepseek_json_completion("system", "user")

        self.assertEqual(payload["role"], "Java Developer")
        request = mock_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(request["max_tokens"], 900)
        self.assertNotIn("extra_body", request)

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_extracts_json_from_fenced_response(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response(
            '```json\n{"company":"Acme Corp"}\n```'
        )

        payload = deepseek_json_completion("system", "user")

        self.assertEqual(payload["company"], "Acme Corp")

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_explicit_json_completion_budget_and_thinking_are_forwarded(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response('{"role":"Java Developer"}')

        payload = deepseek_json_completion(
            "system",
            "user",
            model_name="deepseek-test-model",
            timeout_seconds=12.5,
            max_tokens=2_048,
            thinking="disabled",
        )

        self.assertEqual(payload["role"], "Java Developer")
        self.assertEqual(mock_openai.call_args.kwargs["timeout"], 12.5)
        request = mock_openai.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(request["model"], "deepseek-test-model")
        self.assertEqual(request["max_tokens"], 2_048)
        self.assertEqual(request["extra_body"], {"thinking": {"type": "disabled"}})

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_nonpositive_json_completion_budget_is_rejected_before_request(self, mock_openai) -> None:
        with self.assertRaisesRegex(ValueError, "max_tokens must be positive"):
            deepseek_json_completion("system", "user", max_tokens=0)

        mock_openai.return_value.chat.completions.create.assert_not_called()

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_length_truncation_logs_only_sanitized_diagnostics(self, mock_openai) -> None:
        raw_content = '{"role":"Java Developer","skills":["Java"'
        mock_openai.return_value.chat.completions.create.return_value = _fake_response(
            raw_content,
            finish_reason="length",
            completion_tokens=2_048,
        )

        with self.assertLogs("app.ai.deepseek_client", level="WARNING") as captured:
            with self.assertRaises(DeepSeekJSONError) as raised:
                deepseek_json_completion(
                    "system secret",
                    "user secret",
                    max_tokens=2_048,
                    thinking="disabled",
                )

        self.assertEqual(str(raised.exception), "truncated JSON content")
        self.assertEqual(raised.exception.finish_reason, "length")
        self.assertEqual(raised.exception.raw_content, raw_content)
        logged = "\n".join(captured.output)
        self.assertIn("deepseek_json_parse_failed", logged)
        self.assertIn("finish_reason='length'", logged)
        self.assertIn("max_tokens=2048", logged)
        self.assertIn("completion_tokens=2048", logged)
        self.assertIn(f"response_chars={len(raw_content)}", logged)
        self.assertNotIn(raw_content, logged)
        self.assertNotIn("system secret", logged)
        self.assertNotIn("user secret", logged)

    @patch("app.ai.deepseek_client.settings.deepseek_api_key", "test-key")
    @patch("app.ai.deepseek_client.OpenAI")
    def test_stop_finish_reason_keeps_existing_malformed_error(self, mock_openai) -> None:
        mock_openai.return_value.chat.completions.create.return_value = _fake_response(
            '{"role":',
            finish_reason="stop",
        )

        with self.assertRaisesRegex(DeepSeekJSONError, "DeepSeek returned malformed JSON content") as raised:
            deepseek_json_completion("system", "user")

        self.assertEqual(raised.exception.finish_reason, "stop")


class AIExtractorTests(unittest.TestCase):
    @patch("app.parsing.ai_extractor.extract_sections")
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        return_value={"role_candidates": ["Java Engineer"], "skills": ["Java"]},
    )
    def test_primary_path_does_not_invoke_sectional_extraction(self, _mock_completion, mock_sections) -> None:
        result = extract_ai_job_details("Java Engineer", "Build Java services")

        self.assertIsNone(result.error)
        self.assertEqual(result.extraction_path, "primary")
        mock_sections.assert_not_called()

    @patch(
        "app.parsing.ai_extractor.extract_sections",
        return_value={
            "role_candidates": ["Senior LLMOps Engineer"],
            "primary_location": "Santa Clara, CA",
            "skills": ["Python", "MLOps", "LLMOps", "RAG"],
            "must_have_skills": ["Python", "MLOps", "LLMOps"],
            "confidence": 0.93,
            "evidence": {"skills": ["Strong proficiency in Python and MLOps"]},
        },
    )
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        side_effect=DeepSeekJSONError(
            "truncated JSON content",
            raw_content='{"role_candidates":["Senior LLMOps Engineer"]',
            finish_reason="length",
        ),
    )
    def test_length_truncation_invokes_sectional_extraction(self, _mock_completion, mock_sections) -> None:
        result = extract_ai_job_details("Senior LLMOps Engineer", "Python MLOps LLMOps RAG")

        self.assertIsNone(result.error)
        self.assertEqual(result.extraction_path, "sectional")
        self.assertEqual(result.role_candidates, ("Senior LLMOps Engineer",))
        mock_sections.assert_called_once()

    @patch("app.parsing.ai_extractor.extract_sections")
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        side_effect=DeepSeekJSONError(
            "DeepSeek returned malformed JSON content",
            raw_content='{"role":',
            finish_reason="stop",
        ),
    )
    def test_non_length_json_error_does_not_invoke_sectional_extraction(self, _mock_completion, mock_sections) -> None:
        result = extract_ai_job_details("Subject", "Body")

        self.assertEqual(result.error, "DeepSeek returned malformed JSON content")
        self.assertEqual(result.extraction_path, "primary")
        mock_sections.assert_not_called()

    @patch(
        "app.parsing.ai_extractor.extract_sections",
        side_effect=RuntimeError("skills section failed after 3 attempts: truncated JSON content"),
    )
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        side_effect=DeepSeekJSONError("truncated JSON content", finish_reason="length"),
    )
    def test_sectional_failure_returns_error_for_existing_base_fallback(self, _mock_completion, _mock_sections) -> None:
        result = extract_ai_job_details("Subject", "Body")

        self.assertEqual(result.extraction_path, "sectional")
        self.assertIn("skills section failed", result.error or "")

    @patch(
        "app.parsing.ai_extractor.extract_sections",
        return_value={
            "role_candidates": ["Senior LLMOps / MLOps Engineer"],
            "primary_location": "Santa Clara, CA",
            "work_mode": "onsite",
            "salary_text": "70-75/hr on C2C",
            "experience_years_min": 14,
            "skills": [
                "Python",
                "MLOps",
                "LLMOps",
                "Azure ML",
                "Databricks",
                "Kubernetes",
                "Docker",
                "RAG",
            ],
            "must_have_skills": ["Python", "MLOps", "LLMOps"],
            "nice_to_have_skills": ["RAG"],
            "confidence": 0.94,
            "evidence": {
                "role": ["Senior LLMOps / MLOps Engineer"],
                "skills": ["Strong proficiency in Python", "Experience with Kubernetes, Docker, Azure ML"],
            },
        },
    )
    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        side_effect=DeepSeekJSONError("truncated JSON content", finish_reason="length"),
    )
    def test_real_email_5159_fixture_recovers_after_primary_truncation(self, _mock_completion, _mock_sections) -> None:
        result = extract_ai_job_details(
            REAL_NVOIDS_LLMOPS_CHILD_SUBJECT,
            REAL_NVOIDS_LLMOPS_CHILD_BODY,
            source=REAL_NVOIDS_LLMOPS_CHILD_SOURCE,
        )

        self.assertIsNone(result.error)
        self.assertEqual(result.extraction_path, "sectional")
        self.assertGreaterEqual(len(result.skills_approved) + len(result.skills_unknown), 4)

    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        return_value={
            "role_candidates": ["Senior Java Engineer", "Java Engineer"],
            "company": "Acme Corp",
            "primary_location": "Dallas, TX",
            "mentioned_locations": ["Dallas, TX", "Remote"],
            "work_mode": "hybrid",
            "salary_text": "$80/hr",
            "visa_hints": ["H1B"],
            "experience_years_min": 8,
            "skills_text": "Java, Amazon ECS, Grafana, Temporal",
            "f2f_mentioned": True,
            "asks_contact_fields": True,
            "is_texas_role": True,
            "confidence": 0.86,
            "evidence": {"role": ["Role: Senior Java Engineer"], "skills": ["Java, Amazon ECS, Grafana, Temporal"]},
        },
    )
    def test_returns_structured_result_with_free_skills_and_compatibility_buckets(self, _mock_completion) -> None:
        result = extract_ai_job_details("Senior Java Engineer", "Role: Senior Java Engineer")
        payload = ai_extractor_result_to_payload(result)

        request = _mock_completion.call_args.kwargs
        self.assertEqual(request["max_tokens"], AI_EXTRACTOR_MAX_TOKENS)
        self.assertEqual(request["thinking"], "disabled")
        self.assertEqual(payload["role_candidates"], ["Senior Java Engineer", "Java Engineer"])
        self.assertEqual(payload["company"], "Acme Corp")
        self.assertEqual(payload["primary_location"], "Dallas, TX")
        self.assertEqual(payload["work_mode"], "hybrid")
        self.assertEqual(payload["salary_text"], "$80/hr")
        self.assertEqual(payload["visa_hints"], ["H1B"])
        self.assertEqual(payload["experience_years_min"], 8)
        self.assertEqual(payload["skills_text"], "Java, Amazon ECS, Grafana, Temporal")
        self.assertEqual(payload["must_have_skills"], [])
        self.assertEqual(payload["nice_to_have_skills"], [])
        self.assertTrue(payload["f2f_mentioned"])
        self.assertTrue(payload["asks_contact_fields"])
        self.assertTrue(payload["is_texas_role"])
        self.assertEqual(payload["skills_approved"], ["Java", "Amazon ECS", "Grafana"])
        self.assertEqual(payload["skills_unknown"], ["Temporal"])
        self.assertEqual(payload["confidence"], 0.86)
        self.assertIn("role", payload["evidence"])

    @patch("app.parsing.ai_extractor.deepseek_json_completion", side_effect=RuntimeError("malformed response"))
    def test_handles_malformed_response_with_safe_noop(self, _mock_completion) -> None:
        result = extract_ai_job_details("Subject", "Body")
        payload = ai_extractor_result_to_payload(result)

        self.assertEqual(payload["role_candidates"], [])
        self.assertEqual(payload["skills_text"], "")
        self.assertEqual(payload["must_have_skills"], [])
        self.assertEqual(payload["nice_to_have_skills"], [])
        self.assertEqual(payload["skills_approved"], [])
        self.assertEqual(payload["skills_unknown"], [])
        self.assertFalse(payload["f2f_mentioned"])
        self.assertFalse(payload["asks_contact_fields"])
        self.assertFalse(payload["is_texas_role"])
        self.assertEqual(payload["confidence"], 0.0)
        self.assertEqual(payload["error"], "malformed response")
        self.assertEqual(payload["evidence"]["extractor_error"], ["malformed response"])

    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        return_value={
            "role": "Platform Engineer",
            "skills": ["Python", "RAG"],
            "must_have_skills": "Kubernetes, Python",
            "skills_unknown": ["Temporal"],
        },
    )
    def test_supports_mixed_skill_sources_while_returning_free_skills_text(self, _mock_completion) -> None:
        result = extract_ai_job_details("Platform Engineer", "Body")
        payload = ai_extractor_result_to_payload(result)

        self.assertEqual(payload["role_candidates"], ["Platform Engineer"])
        self.assertEqual(payload["skills_text"], "Python, RAG, Kubernetes, Temporal")
        self.assertEqual(payload["must_have_skills"], ["Kubernetes", "Python"])
        self.assertEqual(payload["nice_to_have_skills"], [])
        self.assertIn("Python", payload["skills_approved"])
        self.assertIn("Kubernetes", payload["skills_approved"])
        self.assertIn("Temporal", payload["skills_unknown"])

    @patch(
        "app.parsing.ai_extractor.deepseek_json_completion",
        return_value={
            "role_candidates": ["Principal Software Engineer Java"],
            "primary_location": "Gwynn Oak, MD",
            "skills_text": "Java, Drools",
            "confidence": 0.51,
            "evidence": {},
        },
    )
    def test_rejects_weak_valid_nvoids_json(self, _mock_completion) -> None:
        body = (
            "Principal Software Engineer Java\n"
            "Design and build cloud-native Twelve-Factor applications.\n"
            "Develop modern UIs using Angular, React, JavaScript, TypeScript.\n"
            "Create microservices with Java, Spring Boot, REST.\n"
            "Implement enterprise integrations using Kafka, SOAP/REST, Web Services."
        )

        result = extract_ai_job_details(
            "Principal Software Engineer Java",
            body,
            source="nvoids",
        )
        payload = ai_extractor_result_to_payload(result)

        self.assertEqual(payload["error"], "weak_ai_extraction: expected at least 4 skills from Nvoids JD row, got 2")
        self.assertEqual(payload["evidence"]["extractor_error"], [payload["error"]])


if __name__ == "__main__":
    unittest.main()
