import unittest

from app.ai.deepseek_client import DeepSeekJSONError, DeepSeekJSONResult
from app.services.role_manifest_service import RoleManifestService


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


class RoleManifestServiceTests(unittest.TestCase):
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
