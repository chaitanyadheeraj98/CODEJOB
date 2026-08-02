import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from instructor.core.exceptions import IncompleteOutputException

from app.parsing.ai_extractor import _AIExtractionSchema
from app.parsing.ai_extractor_sectional import (
    AI_EXTRACTOR_SECTION_BASE_MAX_TOKENS,
    AI_EXTRACTOR_SECTION_MAX_ATTEMPTS,
    CompensationExperienceSection,
    FlagsConfidenceSection,
    RoleLocationSection,
    SkillsSection,
    extract_sections,
)


def _incomplete() -> IncompleteOutputException:
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"skills": ["Python"'), finish_reason="length")]
    )
    return IncompleteOutputException(last_completion=completion)


class AIExtractorSectionalTests(unittest.TestCase):
    @patch(
        "app.parsing.ai_extractor_sectional.SECTION_SPECS",
        (("role_location", RoleLocationSection, "Return role fields."),),
    )
    @patch("app.parsing.ai_extractor_sectional.build_deepseek_instructor_client")
    def test_truncated_section_retries_with_doubled_budget(self, mock_builder) -> None:
        client = MagicMock()
        client.create_with_completion.side_effect = [
            _incomplete(),
            (RoleLocationSection(role_candidates=["Platform Engineer"]), SimpleNamespace()),
        ]
        mock_builder.return_value = client

        payload = extract_sections("system", "user", source="gmail")

        self.assertEqual(payload["role_candidates"], ["Platform Engineer"])
        budgets = [call.kwargs["max_tokens"] for call in client.create_with_completion.call_args_list]
        self.assertEqual(
            budgets,
            [AI_EXTRACTOR_SECTION_BASE_MAX_TOKENS, AI_EXTRACTOR_SECTION_BASE_MAX_TOKENS * 2],
        )

    @patch(
        "app.parsing.ai_extractor_sectional.SECTION_SPECS",
        (("skills", SkillsSection, "Return skill fields."),),
    )
    @patch("app.parsing.ai_extractor_sectional.build_deepseek_instructor_client")
    def test_exhausted_section_aborts_with_safe_error(self, mock_builder) -> None:
        client = MagicMock()
        client.create_with_completion.side_effect = [_incomplete() for _ in range(AI_EXTRACTOR_SECTION_MAX_ATTEMPTS)]
        mock_builder.return_value = client

        with self.assertLogs("app.parsing.ai_extractor_sectional", level="WARNING") as captured:
            with self.assertRaisesRegex(RuntimeError, "skills section failed.*truncated JSON content"):
                extract_sections("private system", "private user", source="nvoids")

        self.assertEqual(client.create_with_completion.call_count, AI_EXTRACTOR_SECTION_MAX_ATTEMPTS)
        self.assertNotIn("private", "\n".join(captured.output))

    @patch("app.parsing.ai_extractor_sectional.build_deepseek_instructor_client")
    def test_sections_merge_into_existing_schema_with_combined_evidence(self, mock_builder) -> None:
        client = MagicMock()
        client.create_with_completion.side_effect = [
            (
                RoleLocationSection(
                    role_candidates=["Senior Engineer"],
                    primary_location="Dallas, TX",
                    work_mode="hybrid",
                    is_texas_role=True,
                    evidence={"role": ["Senior Engineer"]},
                ),
                SimpleNamespace(),
            ),
            (
                CompensationExperienceSection(
                    salary_text="$80/hr",
                    experience_years_min=8,
                    evidence={"experience": ["8 years"]},
                ),
                SimpleNamespace(),
            ),
            (
                SkillsSection(
                    skills=["Python", "Kubernetes"],
                    must_have_skills=["Python"],
                    evidence={"skills": ["Python and Kubernetes"]},
                ),
                SimpleNamespace(),
            ),
            (
                FlagsConfidenceSection(
                    confidence=0.9,
                    evidence={"confidence": ["Direct requirements"]},
                ),
                SimpleNamespace(),
            ),
        ]
        mock_builder.return_value = client

        payload = extract_sections("system", "user", source="gmail")
        validated = _AIExtractionSchema.model_validate(payload)

        self.assertEqual(validated.role_candidates, ["Senior Engineer"])
        self.assertEqual(validated.skills, ["Python", "Kubernetes"])
        self.assertEqual(set(validated.evidence), {"role", "experience", "skills", "confidence"})


if __name__ == "__main__":
    unittest.main()
