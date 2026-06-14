import unittest

from app.phase0 import ai_assist_score, parse_email
from app.skill_taxonomy import (
    compute_intent_weighted_match,
    extract_skills_text,
    normalize_skills_text,
)


AI_ENGINEER_JD = """
Role: AI Engineer
Location: Alpharetta, GA
Implement agentic workflows with clear guardrails, input validation, policy-aware prompting,
human-in-the-loop approvals, approval steps, fallbacks, and safe failure modes.
Develop retrieval and grounding approaches (e.g., retrieval augmented generation) and
tool/function calling patterns for production systems.
Create automated evaluations for quality, groundedness, and safety.
Experience with observability (logs, metrics, traces), content chunking, embeddings,
REST APIs, event-driven patterns, and Secure SDLC practices.
Proficiency in Python, Java, or TypeScript.
"""


class SkillTaxonomyTests(unittest.TestCase):
    def test_normalize_skills_text_maps_aliases_to_canonical_names(self) -> None:
        normalized = normalize_skills_text(
            "java, retrieval augmented generation, function calling, human in the loop, ci/cd"
        )
        self.assertEqual(
            normalized,
            "Java, RAG, Tool Calling, Human-in-the-Loop, CI/CD",
        )

    def test_extract_skills_text_reads_richer_ai_jd_skill_set(self) -> None:
        skills_text = extract_skills_text(AI_ENGINEER_JD)
        self.assertIn("RAG", skills_text)
        self.assertIn("Tool Calling", skills_text)
        self.assertIn("Human-in-the-Loop", skills_text)
        self.assertIn("Observability", skills_text)
        self.assertIn("Embeddings", skills_text)
        self.assertIn("Secure SDLC", skills_text)
        self.assertIn("Python", skills_text)
        self.assertIn("Java", skills_text)
        self.assertIn("TypeScript", skills_text)

    def test_parse_email_no_longer_collapses_ai_jd_to_three_skills(self) -> None:
        parsed = parse_email("", AI_ENGINEER_JD)
        extracted = [item.strip() for item in str(parsed["skills_text"]).split(",") if item.strip()]
        self.assertGreaterEqual(len(extracted), 8)
        self.assertIn("RAG", parsed["skills_text"])
        self.assertIn("Tool Calling", parsed["skills_text"])
        self.assertIn("Human-in-the-Loop", parsed["skills_text"])

    def test_parse_email_extracts_ai_role_and_inline_location(self) -> None:
        parsed = parse_email(
            "",
            "Hi recruiter. Role: AI Engineer Location: Alpharetta, GA Duration: Long term. Need RAG and tool calling.",
        )
        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertEqual(parsed["location"], "Alpharetta, GA")

    def test_ai_assist_score_uses_taxonomy_weighted_skill_bonus(self) -> None:
        class Settings:
            role_keywords = ""
            free_text_guidance = ""

        score, summary = ai_assist_score(
            {
                "role": "AI Engineer",
                "skills_text": "RAG, Tool Calling, Human-in-the-Loop, Observability, Embeddings, Python",
            },
            Settings(),
        )
        self.assertGreater(score, 0.56)
        self.assertIn("AI fit score computed", summary)

    def test_intent_weighted_match_penalizes_ai_exposure_wording(self) -> None:
        strong = compute_intent_weighted_match(
            jd_role="AI Engineer",
            jd_skills_text="RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability",
            resume_skills_text="RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Prompt Engineering",
        )
        weak = compute_intent_weighted_match(
            jd_role="AI Engineer",
            jd_skills_text="RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability",
            resume_skills_text="Generative AI, AI exposure, Agentic AI Concepts, AI-Assisted Engineering, Observability",
        )
        self.assertGreater(strong.score, weak.score)
        self.assertGreater(strong.specialization_score, weak.specialization_score)
        self.assertTrue(weak.weak_signal_hits)


if __name__ == "__main__":
    unittest.main()
