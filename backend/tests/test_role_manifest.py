import json
import unittest
from unittest.mock import MagicMock, patch

import instructor
from instructor.core.exceptions import IncompleteOutputException
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from app.ai.deepseek_client import DeepSeekJSONError, DeepSeekJSONResult
from app.services.role_manifest_service import (
    ROLE_MANIFEST_MAX_TOKENS,
    RoleManifest,
    RoleManifestService,
    _default_provider,
)
from role_manifest_fixtures import (
    REAL_NVOIDS_SIX_ROLE_MANIFEST,
    REAL_NVOIDS_SIX_ROLE_SOURCE,
    REAL_NVOIDS_SIX_ROLE_TITLES,
    REAL_NVOIDS_THREE_ROLE_MANIFEST,
    REAL_NVOIDS_THREE_ROLE_SOURCE,
    REAL_NVOIDS_THREE_ROLE_TITLES,
)


DELOITTE_SOURCE = """Please share resumes along with your LinkedIn URL
VISA: USC/GC Only
1. AI Pod Product Owner
Job ID: DLTJP00057258
14+ years total experience and 10+ years US experience
2. Knowledge Engineer - AI Architect
Job ID: DLTJP00057259
14+ years total experience and 10+ years US experience
3. Microsoft - SharePoint Architect
Job ID: DLTJP00057276
14+ years total experience and 10+ years US experience
4. AI Architect
Job ID: DLTJP00057277
14+ years total experience and 10+ years US experience
5. Business Analyst
Job ID: DLTJP00057269
14+ years total experience and 10+ years US experience
6. Sr OneStream Developer
Job ID: DLTJP00052466
12+ years total experience and 10+ years US experience"""


def _completion(content: str, *, finish_reason: str = "stop") -> ChatCompletion:
    return ChatCompletion(
        id="chatcmpl-test",
        choices=[
            Choice(
                finish_reason=finish_reason,
                index=0,
                logprobs=None,
                message=ChatCompletionMessage(content=content, role="assistant"),
            )
        ],
        created=0,
        model="deepseek-v4-flash",
        object="chat.completion",
    )


def _instructor_client(*responses: ChatCompletion):
    raw_client = OpenAI(api_key="test", base_url="http://test.invalid")
    raw_client.chat.completions.create = MagicMock(side_effect=responses)
    return instructor.from_openai(raw_client, mode=instructor.Mode.JSON), raw_client.chat.completions.create


class RoleManifestServiceTests(unittest.TestCase):
    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_default_provider_keeps_role_manifest_request_defaults(self, mock_builder) -> None:
        manifest = RoleManifest(
            classification="single",
            role_count=1,
            confidence=0.95,
            roles=[
                {
                    "index": 1,
                    "title_hint": "AI Pod Product Owner",
                    "start_line": 3,
                    "end_line": 5,
                    "confidence": 0.95,
                }
            ],
        )
        completion = _completion(manifest.model_dump_json())
        mock_client = MagicMock()
        mock_client.create_with_completion.return_value = (manifest, completion)
        mock_builder.return_value = mock_client

        result = _default_provider("system", "user")

        self.assertEqual(result.payload, manifest.model_dump(mode="json"))
        self.assertEqual(result.finish_reason, "stop")
        mock_builder.assert_called_once_with(timeout_seconds=30.0)
        kwargs = mock_client.create_with_completion.call_args.kwargs
        self.assertEqual(kwargs["max_retries"], 1)
        self.assertEqual(kwargs["max_tokens"], ROLE_MANIFEST_MAX_TOKENS)
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["extra_body"], {"thinking": {"type": "disabled"}})
        self.assertIs(kwargs["response_model"], RoleManifest)

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_incomplete_output_degrades_to_truncated_json(self, mock_builder) -> None:
        partial_content = '{"classification":"multiple","private_source_marker":"must-not-leak"'
        completion = _completion(partial_content, finish_reason="length")
        mock_client = MagicMock()
        mock_client.create_with_completion.side_effect = IncompleteOutputException(last_completion=completion)
        mock_builder.return_value = mock_client

        result = RoleManifestService().detect(f"private-source-marker\n{DELOITTE_SOURCE}")

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.diagnostics.error_category, "truncated_json")
        self.assertEqual(result.diagnostics.finish_reason, "length")
        self.assertEqual(result.error, "The output is incomplete due to a max_tokens length limit.")
        self.assertNotIn("private-source-marker", result.error or "")
        self.assertNotIn("must-not-leak", result.error or "")
        self.assertEqual(mock_client.create_with_completion.call_count, 1)

    def test_large_source_incomplete_output_degrades_to_truncated_json(self) -> None:
        completion = _completion('{"private_source_marker":"must-not-leak"', finish_reason="length")

        def provider(_system: str, _user: str):
            raise IncompleteOutputException(last_completion=completion)

        result = RoleManifestService(
            provider=provider,
            max_window_lines=20,
            window_overlap_lines=5,
        ).detect("\n".join(f"private-source-line-{index}" for index in range(40)))

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.diagnostics.error_category, "truncated_json")
        self.assertEqual(result.diagnostics.finish_reason, "length")
        self.assertNotIn("private-source", result.error or "")
        self.assertNotIn("must-not-leak", result.error or "")

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_real_instructor_length_response_degrades_to_invalid(self, mock_builder) -> None:
        client, create = _instructor_client(
            _completion('{"classification":"multiple","role_count":6', finish_reason="length"),
        )
        mock_builder.return_value = client

        result = RoleManifestService().detect(REAL_NVOIDS_SIX_ROLE_SOURCE)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.diagnostics.error_category, "truncated_json")
        self.assertEqual(result.diagnostics.finish_reason, "length")
        self.assertEqual(create.call_count, 1)

    def test_shared_constraint_without_evidence_is_dropped_not_invalid(self) -> None:
        payload = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "shared_constraints": [
                {"type": "location", "value": "Remote", "start_line": 1, "end_line": 1},
            ],
            "roles": [
                {
                    "index": 1,
                    "title_hint": "Senior Engineer",
                    "start_line": 1,
                    "end_line": 2,
                    "confidence": 0.95,
                }
            ],
        }

        def provider(_system: str, _user: str):
            return payload

        result = RoleManifestService(provider=provider, repair_attempts=0).detect(
            "Senior Engineer position\nFull time role"
        )

        self.assertEqual(result.status, "single")
        self.assertIsNone(result.error)
        self.assertEqual(result.inherited_constraints, ())
        self.assertEqual(len(result.requirements), 1)

    def test_local_validation_failure_maps_to_validation_failure_category(self) -> None:
        payload = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.50,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "Senior Engineer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                }
            ],
        }

        def provider(_system: str, _user: str):
            return payload

        result = RoleManifestService(provider=provider, repair_attempts=0).detect("Senior Engineer position")

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.diagnostics.error_category, "validation_failure")
        self.assertEqual(result.error, "Manifest confidence is below threshold")

    def test_instructor_retry_surfaces_specific_validation_error(self) -> None:
        valid = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "AI Pod Product Owner",
                    "start_line": 3,
                    "end_line": 5,
                    "confidence": 0.95,
                }
            ],
        }
        client, create = _instructor_client(
            _completion('{"classification":"single","role_count":"many","confidence":0.95,"roles":[]}'),
            _completion(json.dumps(valid)),
        )

        result = client.create(
            model="deepseek-v4-flash",
            messages=[{"role": "user", "content": "Extract the role manifest"}],
            response_model=RoleManifest,
            max_retries=1,
        )

        self.assertEqual(result.role_count, 1)
        self.assertEqual(create.call_count, 2)
        retry_messages = create.call_args_list[1].kwargs["messages"]
        retry_text = "\n".join(str(message.get("content", "")) for message in retry_messages)
        self.assertIn("role_count", retry_text)
        self.assertIn("validation error", retry_text.casefold())

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_default_provider_reports_successful_instructor_repair(self, mock_builder) -> None:
        valid = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "AI Pod Product Owner",
                    "start_line": 3,
                    "end_line": 5,
                    "confidence": 0.95,
                }
            ],
        }
        client, create = _instructor_client(
            _completion('{"classification":"single","role_count":"many","confidence":0.95,"roles":[]}'),
            _completion(json.dumps(valid)),
        )
        mock_builder.return_value = client

        result = _default_provider("system", "user")

        self.assertEqual(result.payload["role_count"], 1)
        self.assertTrue(result.repair_attempted)
        self.assertEqual(create.call_count, 2)

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_exhausted_instructor_retries_degrade_to_invalid(self, mock_builder) -> None:
        client, create = _instructor_client(
            _completion('{"classification":"multiple","role_count":"many","confidence":0.95,"roles":[]}'),
            _completion('{"classification":"multiple","role_count":"many","confidence":0.95,"roles":[]}'),
        )
        mock_builder.return_value = client

        result = RoleManifestService().detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.requirements, ())
        self.assertEqual(result.diagnostics.error_category, "schema_failure")
        self.assertTrue(result.diagnostics.repair_attempted)
        self.assertEqual(result.diagnostics.model, "deepseek-v4-flash")
        self.assertEqual(result.error, "Role manifest schema validation failed after retries")
        self.assertNotIn("classification", result.error or "")
        self.assertEqual(create.call_count, 2)

    def test_validated_manifest_materializes_six_bounded_roles(self) -> None:
        provider_payload = {
            "classification": "multiple",
            "role_count": 6,
            "confidence": 0.96,
            "shared_constraints": [
                {"type": "work_authorization", "value": "USC/GC Only", "start_line": 2, "end_line": 2}
            ],
            "roles": [
                {"index": 1, "title_hint": "AI Pod Product Owner", "requisition_id": "DLTJP00057258", "start_line": 3, "end_line": 5, "confidence": 0.98},
                {"index": 2, "title_hint": "Knowledge Engineer - AI Architect", "requisition_id": "DLTJP00057259", "start_line": 6, "end_line": 8, "confidence": 0.98},
                {"index": 3, "title_hint": "Microsoft - SharePoint Architect", "requisition_id": "DLTJP00057276", "start_line": 9, "end_line": 11, "confidence": 0.98},
                {"index": 4, "title_hint": "AI Architect", "requisition_id": "DLTJP00057277", "start_line": 12, "end_line": 14, "confidence": 0.98},
                {"index": 5, "title_hint": "Business Analyst", "requisition_id": "DLTJP00057269", "start_line": 15, "end_line": 17, "confidence": 0.98},
                {"index": 6, "title_hint": "Sr OneStream Developer", "requisition_id": "DLTJP00052466", "start_line": 18, "end_line": 20, "confidence": 0.98},
            ],
        }
        service = RoleManifestService(provider=lambda system, user: provider_payload)

        result = service.detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "multiple")
        self.assertEqual([item.title_hint for item in result.requirements], [
            "AI Pod Product Owner",
            "Knowledge Engineer - AI Architect",
            "Microsoft - SharePoint Architect",
            "AI Architect",
            "Business Analyst",
            "Sr OneStream Developer",
        ])
        self.assertIn("DLTJP00057258", result.requirements[0].source_text)
        self.assertEqual(result.inherited_constraints[0].value, "USC/GC Only")

    def test_real_nvoids_six_role_shape_materializes_without_requisition_ids(self) -> None:
        result = RoleManifestService(
            provider=lambda system, user: REAL_NVOIDS_SIX_ROLE_MANIFEST,
        ).detect(REAL_NVOIDS_SIX_ROLE_SOURCE)

        self.assertEqual(result.status, "multiple")
        self.assertEqual([item.title_hint for item in result.requirements], list(REAL_NVOIDS_SIX_ROLE_TITLES))
        self.assertTrue(all(item.requisition_id == "" for item in result.requirements))
        self.assertNotIn("recruiter group", result.requirements[0].source_text)
        self.assertNotIn("Keywords:", result.requirements[-1].source_text)

    def test_real_nvoids_three_role_shape_does_not_treat_footer_as_a_role(self) -> None:
        result = RoleManifestService(
            provider=lambda system, user: REAL_NVOIDS_THREE_ROLE_MANIFEST,
        ).detect(REAL_NVOIDS_THREE_ROLE_SOURCE)

        self.assertEqual(result.status, "multiple")
        self.assertEqual([item.title_hint for item in result.requirements], list(REAL_NVOIDS_THREE_ROLE_TITLES))
        self.assertEqual(len(result.requirements), 3)
        self.assertNotIn("Keywords:", result.requirements[-1].source_text)

    def test_overlapping_boundaries_fail_closed(self) -> None:
        payload = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.95,
            "roles": [
                {"index": 1, "title_hint": "Role One", "start_line": 3, "end_line": 6, "confidence": 0.95},
                {"index": 2, "title_hint": "Role Two", "start_line": 6, "end_line": 8, "confidence": 0.95},
            ],
        }

        result = RoleManifestService(provider=lambda system, user: payload).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.requirements, ())

    def test_truncated_json_gets_one_in_memory_repair_attempt(self) -> None:
        calls: list[str] = []
        repaired = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [
                {"index": 1, "title_hint": "AI Pod Product Owner", "start_line": 3, "end_line": 5, "confidence": 0.95}
            ],
        }

        def provider(system: str, user: str):
            calls.append(user)
            if len(calls) == 1:
                raise DeepSeekJSONError("truncated JSON content", raw_content='{"classification":"single"')
            return repaired

        result = RoleManifestService(provider=provider).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single")
        self.assertEqual(len(calls), 2)
        self.assertTrue(result.diagnostics.repair_attempted)
        self.assertNotIn("classification", result.diagnostics.__dict__.get("response_hash", ""))

    def test_large_sources_merge_overlapping_absolute_line_windows(self) -> None:
        source = "\n".join(f"line {index}" for index in range(1, 41))

        def provider(system: str, user: str):
            first_line = int(user.split(":", 1)[0])
            role_start = {1: 2, 16: 18, 31: 33}[first_line]
            return {
                "classification": "single",
                "role_count": 1,
                "confidence": 0.95,
                "roles": [{
                    "index": 1,
                    "title_hint": f"Role {first_line}",
                    "start_line": role_start,
                    "end_line": role_start + 1,
                    "confidence": 0.95,
                }],
            }

        result = RoleManifestService(
            provider=provider,
            max_window_lines=20,
            window_overlap_lines=5,
        ).detect(source)

        self.assertEqual(result.status, "multiple")
        self.assertEqual(len(result.requirements), 3)
        self.assertEqual([item.start_line for item in result.requirements], [2, 18, 33])

    def test_deepseek_result_metadata_is_preserved_in_diagnostics(self) -> None:
        payload = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "AI Pod Product Owner",
                    "start_line": 3,
                    "end_line": 5,
                    "confidence": 0.95,
                }
            ],
        }
        provider_result = DeepSeekJSONResult(
            payload=payload,
            model="deepseek-chat",
            finish_reason="stop",
            prompt_tokens=101,
            completion_tokens=23,
            duration_ms=777,
            response_hash="provider-response-hash",
        )

        result = RoleManifestService(provider=lambda system, user: provider_result).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single")
        self.assertEqual(result.diagnostics.model, "deepseek-chat")
        self.assertEqual(result.diagnostics.finish_reason, "stop")
        self.assertEqual(result.diagnostics.prompt_tokens, 101)
        self.assertEqual(result.diagnostics.completion_tokens, 23)
        self.assertEqual(result.diagnostics.response_hash, "provider-response-hash")
        self.assertFalse(result.diagnostics.repair_attempted)


if __name__ == "__main__":
    unittest.main()
