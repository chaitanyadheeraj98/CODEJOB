import json
import unittest
from unittest.mock import MagicMock, patch

import instructor
from instructor.core.exceptions import IncompleteOutputException, InstructorRetryException
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import ValidationError

from app.ai.deepseek_client import DeepSeekJSONResult
from app.config import settings
from app.services.role_manifest_service import (
    ROLE_MANIFEST_MAX_TOKENS,
    RoleManifest,
    RoleManifestService,
    _default_provider,
    _groq_provider,
    _reconcile_passes,
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
    def setUp(self) -> None:
        self._role_manifest_settings = {
            name: getattr(settings, name)
            for name in (
                "deepseek_model_fast",
                "deepseek_model_pro",
                "groq_api_key",
                "role_manifest_extraction_passes_deterministic",
                "role_manifest_extraction_passes_variance",
                "role_manifest_retry_temperature",
                "role_manifest_max_calls_per_email",
                "role_manifest_max_source_chars",
            )
        }
        settings.deepseek_model_fast = "deepseek-v4-flash"
        settings.deepseek_model_pro = "deepseek-v4-pro"
        settings.groq_api_key = ""
        settings.role_manifest_extraction_passes_deterministic = 1
        settings.role_manifest_extraction_passes_variance = 2
        settings.role_manifest_retry_temperature = 0.4
        settings.role_manifest_max_calls_per_email = 12
        settings.role_manifest_max_source_chars = 12000

    def tearDown(self) -> None:
        for name, value in self._role_manifest_settings.items():
            setattr(settings, name, value)

    def test_empty_source_remains_invalid_without_provider_calls(self) -> None:
        provider = MagicMock()

        result = RoleManifestService(provider=provider).detect("  \n\t")

        self.assertEqual(result.status, "invalid")
        self.assertEqual(result.error, "Source text is empty")
        provider.assert_not_called()

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

        result = _default_provider("system", "user", model="deepseek-v4-flash", temperature=0.0)

        self.assertEqual(result.payload, manifest.model_dump(mode="json"))
        self.assertEqual(result.finish_reason, "stop")
        mock_builder.assert_called_once_with(timeout_seconds=30.0)
        kwargs = mock_client.create_with_completion.call_args.kwargs
        self.assertEqual(kwargs["max_retries"], 0)
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

        result = RoleManifestService(max_rung=1).detect(f"private-source-marker\n{DELOITTE_SOURCE}")

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
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

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.diagnostics.error_category, "truncated_json")
        self.assertEqual(result.diagnostics.finish_reason, "length")
        self.assertNotIn("private-source", result.error or "")
        self.assertNotIn("must-not-leak", result.error or "")

    def test_large_source_duplicate_role_with_bad_boundary_in_overlap_window_is_merged_not_rejected(self) -> None:
        # max_window_lines has a 20-line floor (RoleManifestService.__init__), so the
        # source must exceed that to exercise the windowed/chunked _detect_large_source path.
        lines = [f"line-{index}" for index in range(1, 29)]  # 28 lines
        call_count = {"n": 0}

        def provider(_system: str, _user: str):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "classification": "multiple",
                    "role_count": 2,
                    "confidence": 0.95,
                    "roles": [
                        {"index": 1, "title_hint": "Role One", "start_line": 2, "end_line": 8, "confidence": 0.95},
                        {"index": 2, "title_hint": "Role Two", "start_line": 10, "end_line": 18, "confidence": 0.95},
                    ],
                }
            return {
                "classification": "multiple",
                "role_count": 2,
                "confidence": 0.95,
                "roles": [
                    # Duplicate of "Role Two" from window 1, but the provider mis-reports
                    # its start_line as relative-to-window (1) instead of absolute (>=16),
                    # mirroring the real email-5080 failure.
                    {"index": 1, "title_hint": "Role Two", "start_line": 1, "end_line": 18, "confidence": 0.9},
                    {"index": 2, "title_hint": "Role Three", "start_line": 20, "end_line": 28, "confidence": 0.95},
                ],
            }

        result = RoleManifestService(
            provider=provider,
            max_window_lines=20,
            window_overlap_lines=5,
        ).detect("\n".join(lines))

        self.assertEqual(result.status, "multiple")
        self.assertIsNone(result.error)
        titles = sorted(role.title_hint for role in result.manifest.roles)
        self.assertEqual(titles, ["Role One", "Role Three", "Role Two"])
        role_two = next(role for role in result.manifest.roles if role.title_hint == "Role Two")
        self.assertEqual((role_two.start_line, role_two.end_line), (10, 18))

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_real_instructor_length_response_degrades_to_invalid(self, mock_builder) -> None:
        client, create = _instructor_client(
            _completion('{"classification":"multiple","role_count":6', finish_reason="length"),
        )
        mock_builder.return_value = client

        result = RoleManifestService(max_rung=1).detect(REAL_NVOIDS_SIX_ROLE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
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

        result = RoleManifestService(provider=provider).detect(
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

        result = RoleManifestService(provider=provider).detect("Senior Engineer position")

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
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
    def test_default_provider_does_not_hide_an_extra_instructor_retry(self, mock_builder) -> None:
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

        with self.assertRaises(InstructorRetryException):
            _default_provider("system", "user", model="deepseek-v4-flash", temperature=0.0)

        self.assertEqual(create.call_count, 1)

    @patch("app.services.role_manifest_service.build_deepseek_instructor_client")
    def test_exhausted_provider_degrades_to_single_fallback(self, mock_builder) -> None:
        client, create = _instructor_client(
            _completion('{"classification":"multiple","role_count":"many","confidence":0.95,"roles":[]}'),
            _completion('{"classification":"multiple","role_count":"many","confidence":0.95,"roles":[]}'),
        )
        mock_builder.return_value = client

        result = RoleManifestService(max_rung=1).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.diagnostics.error_category, "schema_failure")
        self.assertFalse(result.diagnostics.repair_attempted)
        self.assertEqual(result.diagnostics.model, "deepseek-v4-flash")
        self.assertEqual(result.error, "Role manifest schema validation failed after retries")
        self.assertNotIn("classification", result.error or "")
        self.assertEqual(create.call_count, 1)

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

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)

    def test_flattened_single_line_roles_split_by_verbatim_snippet(self) -> None:
        # Regression test for Email 6164: an HTML-flattening ingestion artifact collapsed two
        # genuinely distinct roles onto one physical line, so the model reports identical
        # start_line/end_line for both. Line-based validation correctly can't disambiguate that
        # (see test_overlapping_boundaries_fail_closed), but each role's own verbatim
        # start_snippet lets the roles be split by character position instead.
        source = (
            "Senior Java Developer with Spring Boot required 10 years experience. "
            "Role Overview: We are seeking a highly skilled AIML Engineer to design AI systems."
        )
        payload = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "Senior Java Developer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    "start_snippet": "Senior Java Developer with Spring Boot",
                },
                {
                    "index": 2,
                    "title_hint": "AIML Engineer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    "start_snippet": "Role Overview: We are seeking",
                },
            ],
        }

        result = RoleManifestService(provider=lambda system, user: payload).detect(source)

        self.assertEqual(result.status, "multiple")
        self.assertEqual(len(result.requirements), 2)
        self.assertEqual([item.title_hint for item in result.requirements], ["Senior Java Developer", "AIML Engineer"])
        self.assertIn("Senior Java Developer with Spring Boot", result.requirements[0].source_text)
        self.assertNotIn("AIML Engineer", result.requirements[0].source_text)
        self.assertIn("Role Overview: We are seeking a highly skilled AIML Engineer", result.requirements[1].source_text)
        self.assertNotIn("Senior Java Developer", result.requirements[1].source_text)

    def test_snippet_resolution_falls_back_to_line_validation_when_snippet_not_found(self) -> None:
        source = "Senior Java Developer required. Role Overview: seeking an AIML Engineer."
        payload = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "Senior Java Developer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    "start_snippet": "Senior Java Developer required.",
                },
                {
                    "index": 2,
                    "title_hint": "AIML Engineer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    # Paraphrased, not a verbatim substring -- resolution must not use it.
                    "start_snippet": "We need an AI/ML engineer",
                },
            ],
        }

        result = RoleManifestService(provider=lambda system, user: payload).detect(source)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)

    def test_snippet_resolution_falls_back_when_two_roles_resolve_to_same_offset(self) -> None:
        source = "Senior Java Developer required. Role Overview: seeking an AIML Engineer."
        payload = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.95,
            "roles": [
                {
                    "index": 1,
                    "title_hint": "Senior Java Developer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    "start_snippet": "Senior Java Developer required.",
                },
                {
                    "index": 2,
                    "title_hint": "AIML Engineer",
                    "start_line": 1,
                    "end_line": 1,
                    "confidence": 0.95,
                    # Same snippet as role one -- can't be disambiguated safely.
                    "start_snippet": "Senior Java Developer required.",
                },
            ],
        }

        result = RoleManifestService(provider=lambda system, user: payload).detect(source)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)

    def test_jittered_rung_changes_temperature_and_reconciles_two_passes(self) -> None:
        calls: list[tuple[str, float]] = []
        valid = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [
                {"index": 1, "title_hint": "AI Pod Product Owner", "start_line": 3, "end_line": 5, "confidence": 0.95}
            ],
        }
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}

        def provider(_system: str, _user: str, *, model: str, temperature: float):
            calls.append((model, temperature))
            return uncertain if len(calls) == 1 else valid

        with patch("app.services.role_manifest_service._default_provider", side_effect=provider):
            result = RoleManifestService(max_rung=2).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single")
        self.assertEqual(
            calls,
            [
                ("deepseek-v4-flash", 0.0),
                ("deepseek-v4-flash", 0.4),
                ("deepseek-v4-flash", 0.4),
            ],
        )
        self.assertEqual(result.diagnostics.calls_spent, 3)
        self.assertTrue(result.diagnostics.passes_reconciled)
        self.assertTrue(result.diagnostics.repair_attempted)

    def test_deterministic_rungs_run_one_pass_and_variance_rung_runs_two(self) -> None:
        calls: list[tuple[str, float]] = []
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}

        def provider(_system: str, _user: str, *, model: str, temperature: float):
            calls.append((model, temperature))
            return uncertain

        with patch("app.services.role_manifest_service._default_provider", side_effect=provider):
            result = RoleManifestService(max_rung=3).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(
            calls,
            [
                ("deepseek-v4-flash", 0.0),
                ("deepseek-v4-flash", 0.4),
                ("deepseek-v4-flash", 0.4),
                ("deepseek-v4-pro", 0.0),
            ],
        )

    def test_multi_pass_disagreement_escalates_to_pro_rung(self) -> None:
        single = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [{"index": 1, "title_hint": "AI Pod Product Owner", "start_line": 3, "end_line": 5, "confidence": 0.95}],
        }
        multiple = {
            "classification": "multiple",
            "role_count": 2,
            "confidence": 0.95,
            "roles": [
                {"index": 1, "title_hint": "Role One", "start_line": 3, "end_line": 5, "confidence": 0.95},
                {"index": 2, "title_hint": "Role Two", "start_line": 6, "end_line": 8, "confidence": 0.95},
            ],
        }
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        payloads = iter((uncertain, single, multiple, single))
        calls: list[str] = []

        def provider(_system: str, _user: str, *, model: str, temperature: float):
            calls.append(model)
            return next(payloads)

        with patch("app.services.role_manifest_service._default_provider", side_effect=provider):
            result = RoleManifestService(max_rung=3).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single")
        self.assertEqual(calls[-1], "deepseek-v4-pro")
        self.assertEqual(result.diagnostics.calls_spent, 4)

    def test_reconcile_passes_requires_matching_role_identity_and_count(self) -> None:
        first = RoleManifest.model_validate(
            {
                "classification": "single",
                "role_count": 1,
                "confidence": 0.95,
                "roles": [{"index": 1, "title_hint": "AI/ML Engineer", "start_line": 1, "end_line": 1, "confidence": 0.95}],
            }
        )
        matching = first.model_copy(deep=True)
        matching.roles[0].title_hint = "AI ML Engineer"
        disagreeing = RoleManifest(
            classification="multiple",
            role_count=2,
            confidence=0.95,
            roles=[
                {"index": 1, "title_hint": "AI ML Engineer", "start_line": 1, "end_line": 1, "confidence": 0.95},
                {"index": 2, "title_hint": "Data Engineer", "start_line": 2, "end_line": 2, "confidence": 0.95},
            ],
        )

        self.assertIs(_reconcile_passes([first, matching]), first)
        self.assertIsNone(_reconcile_passes([first, disagreeing]))

    @patch("app.services.role_manifest_service.groq_chat_json")
    def test_groq_provider_rejects_json_that_fails_role_manifest_validation(self, mock_groq) -> None:
        mock_groq.return_value = (
            {"classification": "single", "role_count": "many", "confidence": 0.95, "roles": []},
            None,
        )

        with self.assertRaises(ValidationError):
            _groq_provider("system", "user")

        self.assertEqual(mock_groq.call_args.kwargs["model"], settings.role_manifest_groq_model)
        self.assertEqual(mock_groq.call_args.kwargs["max_tokens"], settings.role_manifest_max_tokens_groq)

    def test_full_ladder_exhaustion_returns_one_bounded_fallback(self) -> None:
        """Four rungs, six calls, one bounded fallback - with the default final rung.

        The final rung is DeepSeek Pro with thinking enabled rather than Groq, so the
        whole ladder is one vendor. The call *budget* is unchanged; only who answers
        the last rung is.
        """
        settings.groq_api_key = "test-key"
        settings.role_manifest_final_rung = "deepseek_pro"
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        with (
            patch("app.services.role_manifest_service._default_provider", return_value=uncertain) as deepseek,
            patch("app.services.role_manifest_service._groq_provider", return_value=uncertain) as groq,
        ):
            result = RoleManifestService().detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.requirements[0].source_text, DELOITTE_SOURCE)
        self.assertEqual(result.diagnostics.calls_spent, 6)
        self.assertEqual(deepseek.call_count, 6)
        groq.assert_not_called()
        self.assertEqual(
            result.diagnostics.rungs_tried,
            "deepseek_fast_deterministic,deepseek_fast_variance,deepseek_pro_deterministic,deepseek_pro_independent",
        )

    def test_role_manifest_final_rung_selection(self) -> None:
        """All three values produce the expected ladder.

        `groq` is the rollback and must keep working; `off` must drop the rung rather
        than silently substituting one, or the ladder would quietly get shorter than
        the setting says.
        """
        settings.groq_api_key = "test-key"
        settings.deepseek_model_pro = "deepseek-v4-pro"
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}

        for final_rung, expected_last, groq_expected in (
            ("deepseek_pro", "deepseek_pro_independent", 0),
            ("groq", "groq_independent", 2),
            ("off", "deepseek_pro_deterministic", 0),
        ):
            with self.subTest(final_rung=final_rung):
                settings.role_manifest_final_rung = final_rung
                with (
                    patch("app.services.role_manifest_service._default_provider", return_value=uncertain),
                    patch("app.services.role_manifest_service._groq_provider", return_value=uncertain) as groq,
                ):
                    result = RoleManifestService().detect(DELOITTE_SOURCE)
                self.assertTrue(
                    result.diagnostics.rungs_tried.endswith(expected_last),
                    result.diagnostics.rungs_tried,
                )
                self.assertEqual(groq.call_count, groq_expected)

    def test_manifest_rungs_pass_thinking_explicitly(self) -> None:
        """Guards the regression this ladder was quietly exposed to.

        Thinking mode is on by default and ignores `temperature`. A ladder whose
        rungs 1-2 differ only in temperature therefore collapses into the same call
        run twice at double cost unless thinking is explicitly disabled - and the
        final rung is only a genuinely independent opinion if thinking is explicitly
        enabled there.
        """
        settings.groq_api_key = ""
        settings.deepseek_model_pro = "deepseek-v4-pro"
        settings.role_manifest_final_rung = "deepseek_pro"
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        with patch(
            "app.services.role_manifest_service._default_provider", return_value=uncertain
        ) as provider:
            RoleManifestService().detect(DELOITTE_SOURCE)

        thinking_by_model = [
            (call.kwargs["model"], call.kwargs.get("thinking", "disabled")) for call in provider.call_args_list
        ]
        self.assertTrue(thinking_by_model, "the ladder must have run")
        # Rungs 1-3 deterministic/variance: thinking off, so temperature is honoured.
        self.assertTrue(
            all(thinking == "disabled" for _model, thinking in thinking_by_model[:4]),
            thinking_by_model,
        )
        # Rung 4: a different reasoning path, not a fourth sample of the same one.
        self.assertEqual(thinking_by_model[-1], ("deepseek-v4-pro", "enabled"))

    def test_default_provider_passes_thinking_into_extra_body(self) -> None:
        """The wiring itself, at the one place the API actually reads it."""
        manifest = RoleManifest(classification="single", role_count=1, confidence=0.9, roles=[])
        response = MagicMock()
        response.choices = []
        response.usage = None
        client = MagicMock()
        client.create_with_completion.return_value = (manifest, response)
        with patch(
            "app.services.role_manifest_service.build_deepseek_instructor_client", return_value=client
        ):
            _default_provider("system", "user", model="m", temperature=0.0, thinking="enabled")
        self.assertEqual(
            client.create_with_completion.call_args.kwargs["extra_body"],
            {"thinking": {"type": "enabled"}},
        )

    def test_background_ladder_never_invokes_pro_or_groq_rungs(self) -> None:
        settings.groq_api_key = "test-key"
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        calls: list[str] = []

        def provider(_system: str, _user: str, *, model: str, temperature: float):
            calls.append(model)
            return uncertain

        with (
            patch("app.services.role_manifest_service._default_provider", side_effect=provider),
            patch("app.services.role_manifest_service._groq_provider") as groq,
        ):
            result = RoleManifestService(max_rung=2).detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(calls, ["deepseek-v4-flash"] * 3)
        groq.assert_not_called()

    def test_missing_optional_pro_and_groq_rungs_degrades_cleanly(self) -> None:
        settings.deepseek_model_pro = ""
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        with patch("app.services.role_manifest_service._default_provider", return_value=uncertain) as provider:
            result = RoleManifestService().detect(DELOITTE_SOURCE)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(provider.call_count, 3)
        self.assertNotIn("deepseek_pro", result.diagnostics.rungs_tried)
        self.assertNotIn("groq", result.diagnostics.rungs_tried)

    def test_large_source_stops_at_per_email_call_ceiling(self) -> None:
        settings.groq_api_key = "test-key"
        settings.role_manifest_max_calls_per_email = 4
        uncertain = {"classification": "uncertain", "role_count": 0, "confidence": 0.2, "roles": []}
        source = "\n".join(f"line {index}" for index in range(1, 46))
        with (
            patch("app.services.role_manifest_service._default_provider", return_value=uncertain) as deepseek,
            patch("app.services.role_manifest_service._groq_provider") as groq,
        ):
            result = RoleManifestService(max_window_lines=20, window_overlap_lines=5).detect(source)

        self.assertEqual(result.status, "single_fallback")
        self.assertEqual(result.diagnostics.calls_spent, 4)
        self.assertEqual(deepseek.call_count, 4)
        groq.assert_not_called()
        self.assertEqual(result.requirements[0].source_text, source)

    def test_character_limit_triggers_windowed_detection_for_one_line_source(self) -> None:
        settings.role_manifest_max_source_chars = 20
        source = "Senior Java Developer with a long flattened description"
        payload = {
            "classification": "single",
            "role_count": 1,
            "confidence": 0.95,
            "roles": [{"index": 1, "title_hint": "Senior Java Developer", "start_line": 1, "end_line": 1, "confidence": 0.95}],
        }
        service = RoleManifestService(provider=lambda system, user: payload)

        with patch.object(service, "_detect_large_source", wraps=service._detect_large_source) as windowed:
            result = service.detect(source)

        self.assertEqual(result.status, "single")
        windowed.assert_called_once()

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
