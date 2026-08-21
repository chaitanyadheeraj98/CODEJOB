import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.resume_enrichment_service import enrich_resume


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
        self.assertEqual(resume.content_evidence_json, "{}")


if __name__ == "__main__":
    unittest.main()
