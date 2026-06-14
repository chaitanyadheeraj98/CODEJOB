import unittest

from app.phase0 import (
    ai_assist_score,
    build_skill_source_sections,
    parse_email,
    slice_jd_sections,
    strip_forward_headers,
    strip_recruiter_footer,
)
from app.skill_taxonomy import (
    aggregate_jd_skill_evidence,
    compute_intent_weighted_match,
    extract_jd_skill_evidence,
    extract_jd_skills_text,
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

AI_ENGINEER_SECTIONED_JD = """
Role Summary:
We are seeking an AI Engineer to design responsible AI-powered tools.
Key Responsibilities:
Implement agentic workflows with clear guardrails, policy-aware prompting, and human-in-the-loop approvals.
Develop retrieval and grounding approaches including retrieval augmented generation and tool/function calling.
Required Qualifications:
Experience implementing LLM-powered features, prompt engineering, REST APIs, event-driven patterns, and Secure SDLC.
Technical Skills:
Embeddings, observability, content chunking, Python, Java, TypeScript
Preferred Qualifications:
Familiarity with financial services and production support.
Compliance & Responsible AI Expectations:
Responsible AI, auditability, privacy, and access controls.
Thanks & Regards
Himanshu Verma
Technical Recruiter
Email: himanshu@example.com
1317 Linwood Avenue, Los Angeles, CA 90017
"""

JAVA_FULLSTACK_SECTIONED_JD = """
Role: Java Full Stack Developer
Job Summary:
Build enterprise applications across backend services and modern web interfaces.
Required Qualifications:
Java, Spring Boot, Microservices, REST APIs, React, TypeScript, SQL
Technical Skills:
AWS, CI/CD, Docker, PostgreSQL, Observability
Preferred Qualifications:
Banking, Agile, Production Support
"""


class SkillTaxonomyTests(unittest.TestCase):
    def test_strip_forward_headers_keeps_jd_body(self) -> None:
        cleaned = strip_forward_headers(
            "From: recruiter@example.com\nSent: today\nSubject: AI role\n\nRole: AI Engineer\nNeed RAG and tool calling."
        )
        self.assertNotIn("From:", cleaned)
        self.assertIn("Role: AI Engineer", cleaned)

    def test_strip_recruiter_footer_removes_signature_cluster(self) -> None:
        cleaned = strip_recruiter_footer(
            "Role: AI Engineer\nNeed RAG and embeddings.\n\nThanks & Regards\nHimanshu Verma\nTechnical Recruiter\nEmail: himanshu@example.com\nPhone: 123-456-7890"
        )
        self.assertIn("Need RAG and embeddings.", cleaned)
        self.assertNotIn("Technical Recruiter", cleaned)
        self.assertNotIn("himanshu@example.com", cleaned)

    def test_strip_recruiter_footer_does_not_trim_regular_jd_content(self) -> None:
        body = (
            "Role: AI Engineer\n"
            "Partner with recruiter operations teams and security reviewers.\n"
            "Need observability, RAG, and Secure SDLC."
        )
        cleaned = strip_recruiter_footer(body)
        self.assertEqual(cleaned, body)

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

    def test_safe_failure_modes_does_not_extract_safe_framework(self) -> None:
        skills_text = extract_skills_text("Implement safe failure modes and safe retries.")
        self.assertNotIn("SAFe", skills_text)

    def test_services_alone_does_not_extract_angular_services(self) -> None:
        skills_text = extract_skills_text("Integrate approved APIs with internal services and data sources.")
        self.assertNotIn("Angular Services", skills_text)

    def test_scaled_agile_framework_still_extracts_safe(self) -> None:
        skills_text = extract_skills_text("Experience with scaled agile framework in enterprise delivery.")
        self.assertIn("SAFe", skills_text)

    def test_angular_services_phrase_still_extracts_angular_services(self) -> None:
        skills_text = extract_skills_text("Strong experience with angular services and dependency injection.")
        self.assertIn("Angular Services", skills_text)

    def test_code_review_still_extracts_code_reviews(self) -> None:
        skills_text = extract_skills_text("Participate in code review and peer review across teams.")
        self.assertIn("Code Reviews", skills_text)

    def test_knowledge_grounded_qa_still_extracts_knowledge_qa(self) -> None:
        skills_text = extract_skills_text("Build knowledge grounded qa assistants for operations.")
        self.assertIn("Knowledge Q&A", skills_text)

    def test_extract_jd_skill_evidence_prefers_useful_sections_only(self) -> None:
        sections = build_skill_source_sections(slice_jd_sections(AI_ENGINEER_SECTIONED_JD))
        evidence = extract_jd_skill_evidence(sections)
        names = [item.canonical_name for item in evidence]

        self.assertIn("Agentic Workflows", names)
        self.assertIn("Tool Calling", names)
        self.assertIn("Human-in-the-Loop", names)
        self.assertIn("Embeddings", names)
        self.assertNotIn("Email", names)
        self.assertNotIn("Address", names)

    def test_aggregate_jd_skill_evidence_keeps_required_signals_ahead_of_preferred(self) -> None:
        sections = build_skill_source_sections(slice_jd_sections(AI_ENGINEER_SECTIONED_JD))
        aggregated = aggregate_jd_skill_evidence(extract_jd_skill_evidence(sections))
        ordered = [item.canonical_name for item in aggregated]

        self.assertLess(ordered.index("Tool Calling"), ordered.index("Production Support"))
        self.assertLess(ordered.index("Embeddings"), ordered.index("Production Support"))

    def test_extract_jd_skills_text_returns_rich_ai_skill_string(self) -> None:
        sections = build_skill_source_sections(slice_jd_sections(AI_ENGINEER_SECTIONED_JD))
        skills_text = extract_jd_skills_text(sections, role_text="AI Engineer", fallback_text=AI_ENGINEER_SECTIONED_JD)

        self.assertIn("Agentic Workflows", skills_text)
        self.assertIn("Tool Calling", skills_text)
        self.assertIn("Human-in-the-Loop", skills_text)
        self.assertIn("RAG", skills_text)
        self.assertIn("Prompt Engineering", skills_text)
        self.assertIn("Embeddings", skills_text)
        self.assertIn("Observability", skills_text)
        self.assertIn("Responsible AI", skills_text)
        self.assertIn("Python", skills_text)
        self.assertIn("Java", skills_text)
        self.assertIn("TypeScript", skills_text)
        self.assertIn("REST APIs", skills_text)
        self.assertIn("Secure SDLC", skills_text)
        self.assertNotIn("Technical Recruiter", skills_text)
        self.assertNotIn("Email", skills_text)
        self.assertNotIn("Address", skills_text)

    def test_extract_jd_skills_text_falls_back_when_sections_are_weak(self) -> None:
        skills_text = extract_jd_skills_text([], fallback_text="Need Python, Java, and REST APIs.")
        self.assertIn("Python", skills_text)
        self.assertIn("Java", skills_text)
        self.assertIn("REST APIs", skills_text)

    def test_role_family_filter_suppresses_weak_ai_off_family_noise(self) -> None:
        sections = build_skill_source_sections(slice_jd_sections(AI_ENGINEER_SECTIONED_JD))
        skills_text = extract_jd_skills_text(sections, role_text="AI Engineer", fallback_text=AI_ENGINEER_SECTIONED_JD)

        self.assertIn("Agentic Workflows", skills_text)
        self.assertIn("Prompt Engineering", skills_text)
        self.assertIn("Python", skills_text)
        self.assertIn("Java", skills_text)
        self.assertNotIn("Angular Services", skills_text)
        self.assertNotIn("SAFe", skills_text)

    def test_role_family_filter_preserves_java_fullstack_mixed_stack(self) -> None:
        sections = build_skill_source_sections(slice_jd_sections(JAVA_FULLSTACK_SECTIONED_JD))
        skills_text = extract_jd_skills_text(
            sections,
            role_text="Java Full Stack Developer",
            fallback_text=JAVA_FULLSTACK_SECTIONED_JD,
        )

        self.assertIn("Java", skills_text)
        self.assertIn("Spring Boot", skills_text)
        self.assertIn("REST APIs", skills_text)
        self.assertIn("TypeScript", skills_text)
        self.assertIn("AWS", skills_text)
        self.assertIn("CI/CD", skills_text)
        self.assertIn("PostgreSQL", skills_text)

    def test_role_family_filter_falls_back_to_unfiltered_when_over_pruned(self) -> None:
        jd = """
Role: AI Engineer
Preferred Qualifications:
Angular Services
"""
        sections = build_skill_source_sections(slice_jd_sections(jd))
        skills_text = extract_jd_skills_text(sections, role_text="AI Engineer", fallback_text=jd)
        self.assertIn("Angular Services", skills_text)

    def test_parse_email_no_longer_collapses_ai_jd_to_three_skills(self) -> None:
        parsed = parse_email("", AI_ENGINEER_JD)
        extracted = [item.strip() for item in str(parsed["skills_text"]).split(",") if item.strip()]
        self.assertGreaterEqual(len(extracted), 8)
        self.assertIn("Agentic Workflows", parsed["skills_text"])
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

    def test_parse_email_ignores_recruiter_footer_noise_in_skills(self) -> None:
        parsed = parse_email(
            "",
            (
                "Role: AI Engineer\n"
                "Location: Alpharetta, GA\n"
                "Need RAG, tool calling, human-in-the-loop approvals, observability, and embeddings.\n\n"
                "Thanks & Regards\n"
                "Himanshu Verma\n"
                "Technical Recruiter\n"
                "Email: himanshu@example.com\n"
                "Angular Services, SAFe\n"
            ),
        )
        self.assertIn("RAG", parsed["skills_text"])
        self.assertIn("Tool Calling", parsed["skills_text"])
        self.assertIn("Observability", parsed["skills_text"])
        self.assertNotIn("Angular Services", parsed["skills_text"])
        self.assertNotIn("SAFe", parsed["skills_text"])

    def test_parse_email_uses_section_aware_extraction_when_headings_exist(self) -> None:
        parsed = parse_email("", AI_ENGINEER_SECTIONED_JD)

        self.assertIn("Agentic Workflows", parsed["skills_text"])
        self.assertIn("Prompt Engineering", parsed["skills_text"])
        self.assertIn("Responsible AI", parsed["skills_text"])
        self.assertNotIn("Angular Services", parsed["skills_text"])
        self.assertNotIn("SAFe", parsed["skills_text"])
        self.assertNotIn("Technical Recruiter", parsed["skills_text"])
        self.assertNotIn("Email", parsed["skills_text"])

    def test_parse_email_falls_back_for_headingless_jd(self) -> None:
        parsed = parse_email(
            "Java Developer",
            "Need Python, Java, and REST APIs for production services with observability.",
        )

        self.assertIn("Python", parsed["skills_text"])
        self.assertIn("Java", parsed["skills_text"])
        self.assertIn("REST APIs", parsed["skills_text"])

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
