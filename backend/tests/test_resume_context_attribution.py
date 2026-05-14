import unittest

from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_EXTRACT_FAILED,
    RESUME_CONTEXT_INJECTED,
    RESUME_CONTEXT_LIMITED,
    classify_extracted_resume_context,
)


class ResumeContextAttributionTests(unittest.TestCase):
    def test_classify_injected_when_text_is_usable(self) -> None:
        result = classify_extracted_resume_context("Python Java Spring Boot AWS")
        self.assertEqual(result.status, RESUME_CONTEXT_INJECTED)
        self.assertGreater(result.extracted_chars, 0)

    def test_classify_limited_when_fallback_marker_present(self) -> None:
        result = classify_extracted_resume_context(
            "Resume available as 'resume.doc', but text extraction is limited. "
            "Use only conservative claims and keep the reply concise."
        )
        self.assertEqual(result.status, RESUME_CONTEXT_LIMITED)

    def test_classify_extract_failed_when_text_missing(self) -> None:
        result = classify_extracted_resume_context("")
        self.assertEqual(result.status, RESUME_CONTEXT_EXTRACT_FAILED)

    def test_classify_extract_failed_when_error_present(self) -> None:
        result = classify_extracted_resume_context("anything", extraction_error="boom")
        self.assertEqual(result.status, RESUME_CONTEXT_EXTRACT_FAILED)


if __name__ == "__main__":
    unittest.main()
