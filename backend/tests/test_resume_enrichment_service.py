import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.resume_enrichment_service import backfill_role_and_label, enrich_resume


class FakeLlm:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    def invoke(self, _prompt: str) -> SimpleNamespace:
        return SimpleNamespace(content=next(self.responses))


class ResumeEnrichmentServiceTests(unittest.TestCase):
    def test_enrich_resume_sets_content_summary_and_validated_evidence(self) -> None:
        resume = SimpleNamespace(file_path="resume.pdf", file_name="resume.pdf")
        evidence = {
            "skills": [{"name": "Spring Boot", "evidence": "Built Spring Boot services."}],
            "years_detected": 6,
            "titles": ["Senior Backend Engineer"],
            "certifications": ["AWS Certified Developer"],
            "projects": ["Built payment APIs"],
            "domain": "fintech",
        }
        with (
            patch(
                "app.services.resume_enrichment_service.extract_document_text",
                return_value=SimpleNamespace(markdown_text="# Resume\nBuilt Spring Boot services."),
            ),
            patch(
                "app.services.resume_enrichment_service.build_chat_llm",
                return_value=FakeLlm(["Backend engineer summary.", json.dumps(evidence)]),
            ),
        ):
            enrich_resume(resume)

        self.assertEqual(resume.content_markdown, "# Resume\nBuilt Spring Boot services.")
        self.assertEqual(resume.content_summary, "Backend engineer summary.")
        self.assertEqual(json.loads(resume.content_evidence_json)["years_detected"], 6)
        self.assertEqual(resume.primary_role, "Senior Backend Engineer")
        # The label is normalised on the way in now (normalize_variant_label), so the
        # stored value is the clean one. The raw model answer is still kept verbatim
        # in content_evidence_json.
        self.assertEqual(resume.variant_label, "Fintech")

    def test_enrich_resume_does_not_overwrite_existing_role_or_label(self) -> None:
        resume = SimpleNamespace(
            file_path="resume.pdf",
            file_name="resume.pdf",
            primary_role="Manually Set Role",
            variant_label="Manually Set Label",
        )
        evidence = {"titles": ["Senior Backend Engineer"], "domain": "fintech"}
        with (
            patch(
                "app.services.resume_enrichment_service.extract_document_text",
                return_value=SimpleNamespace(markdown_text="Resume text"),
            ),
            patch(
                "app.services.resume_enrichment_service.build_chat_llm",
                return_value=FakeLlm(["Summary", json.dumps(evidence)]),
            ),
        ):
            enrich_resume(resume)

        self.assertEqual(resume.primary_role, "Manually Set Role")
        self.assertEqual(resume.variant_label, "Manually Set Label")

    def test_malformed_evidence_falls_back_without_raising(self) -> None:
        resume = SimpleNamespace(file_path="resume.pdf", file_name="resume.pdf")
        with (
            patch(
                "app.services.resume_enrichment_service.extract_document_text",
                return_value=SimpleNamespace(markdown_text="Resume text"),
            ),
            patch(
                "app.services.resume_enrichment_service.build_chat_llm",
                return_value=FakeLlm(["Summary", "not json"]),
            ),
        ):
            enrich_resume(resume)

        self.assertEqual(resume.content_summary, "Summary")
        self.assertEqual(json.loads(resume.content_evidence_json)["skills"], [])
        self.assertFalse(hasattr(resume, "primary_role"))

    def test_backfill_role_and_label_uses_existing_evidence_without_calling_llm(self) -> None:
        resume = SimpleNamespace(
            primary_role="",
            variant_label="",
            content_evidence_json=json.dumps({"titles": ["Java Backend Developer"], "domain": "fintech"}),
        )
        with patch("app.services.resume_enrichment_service.build_chat_llm") as build_llm:
            changed = backfill_role_and_label(resume)

        build_llm.assert_not_called()
        self.assertTrue(changed)
        self.assertEqual(resume.primary_role, "Java Backend Developer")
        # The label is normalised on the way in now (normalize_variant_label), so the
        # stored value is the clean one. The raw model answer is still kept verbatim
        # in content_evidence_json.
        self.assertEqual(resume.variant_label, "Fintech")

    def test_backfill_role_and_label_is_noop_without_usable_evidence(self) -> None:
        resume = SimpleNamespace(primary_role="", variant_label="", content_evidence_json="{}")
        self.assertFalse(backfill_role_and_label(resume))
        self.assertEqual(resume.primary_role, "")
        self.assertEqual(resume.variant_label, "")

    def test_backfill_role_and_label_does_not_overwrite_existing_values(self) -> None:
        resume = SimpleNamespace(
            primary_role="Kept Role",
            variant_label="Kept Label",
            content_evidence_json=json.dumps({"titles": ["Other Title"], "domain": "other"}),
        )
        self.assertFalse(backfill_role_and_label(resume))
        self.assertEqual(resume.primary_role, "Kept Role")
        self.assertEqual(resume.variant_label, "Kept Label")


if __name__ == "__main__":
    unittest.main()
