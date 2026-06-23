import unittest

from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService


class ScoringRuntimeServiceTests(unittest.TestCase):
    def test_semantic_text_for_email_compacts_skills(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        text = service.semantic_text_for_email(
            "",
            "Need AI Engineer with strong RAG and tool calling experience.",
            "AI Engineer",
            "RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Python, Java, TypeScript, REST APIs",
        )
        self.assertIn("RoleFamily:ai", text)
        self.assertIn("Clusters:", text)
        self.assertIn("RAG", text)

    def test_semantic_text_for_resume_prefers_manual_skills(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class Resume:
            skills_text = "Java, Spring Boot, AWS"
            file_path = "missing.docx"
            file_name = "missing.docx"

        text = service.semantic_text_for_resume(Resume())
        self.assertIn("Skills:", text)
        self.assertIn("Java", text)
        self.assertIn("Spring Boot", text)
        self.assertIn("AWS", text)

    def test_semantic_text_for_resume_can_skip_file_fallback_for_matching(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class Resume:
            skills_text = ""
            file_path = "missing.docx"
            file_name = "missing.docx"

        text = service.semantic_text_for_resume(Resume(), allow_file_fallback=False)
        self.assertEqual(text, "")

    def test_extract_latest_message_block_prefers_newest_segment(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        body = (
            "Latest update: Java + Spring Boot role in TX.\n"
            "Please review.\n\n"
            "On Thu, May 28, 2026 at 4:11 PM Recruiter wrote:\n"
            "> older thread details"
        )
        latest, source = service._extract_latest_message_block(body)
        self.assertIn("Latest update", latest)
        self.assertEqual(source, "latest_block")

    def test_safe_embed_chunking_for_long_input(self) -> None:
        calls: list[int] = []

        def embed(text: str) -> tuple[list[float], str]:
            calls.append(len(text))
            return [1.0, 2.0], "openrouter"

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=embed))
        vector, payload, provider, chunks = service._safe_embed_with_chunking(None, "x" * 2500)
        self.assertEqual(provider, "openrouter")
        self.assertEqual(chunks, 3)
        self.assertEqual(vector, [1.0, 2.0])
        self.assertTrue(payload is not None)
        self.assertGreaterEqual(len(calls), 2)

    def test_missing_resume_skills_skips_semantic_instead_of_file_extraction(self) -> None:
        def failing_embed(_text: str) -> tuple[list[float], str]:
            raise ValueError("No embedding data received")

        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=failing_embed))
        parsed = {
            "role": "Java Developer",
            "skills_text": "java, spring, spring boot, react",
            "salary_text": "",
            "location": "hybrid",
        }

        class Settings:
            feature_semantic_enabled = True
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            semantic_embedding = None
            file_path = "missing.docx"
            file_name = "missing.docx"

        score, summary, source, _email_embedding, _resume_embedding, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Body " + ("x" * 2000),
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=Resume(),
        )
        self.assertEqual(source, "v2_rules_plus_semantic")
        self.assertIn("semantic skipped (resume text unavailable)", summary)
        self.assertLessEqual(score, 0.18)
        self.assertEqual(diag.fallback_reason, "resume_text_unavailable")

    def test_weak_skills_uses_rich_fallback_keyword_source(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "java",
            "salary_text": "",
            "location": "hybrid",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        score, summary, _source, _e, _r, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Need Java + Spring Boot + React developers",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=None,
        )
        self.assertGreaterEqual(score, 0.54)
        self.assertEqual(diag.keyword_source, "rich_fallback")
        self.assertIn("AI fit score computed", summary)

    def test_thread_snapshot_carry_forward_when_current_is_weaker(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1], "hash")))

        class FakeRow:
            def __init__(self, rid: int, skills: str):
                self.id = rid
                self.skills_text = skills

        class FakeQuery:
            def __init__(self, rows):
                self.rows = rows
            def filter(self, *_args, **_kwargs):
                return self
            def order_by(self, *_args, **_kwargs):
                return self
            def limit(self, *_args, **_kwargs):
                return self
            def all(self):
                return self.rows

        class FakeDb:
            def __init__(self, rows):
                self.rows = rows
            def query(self, _model):
                return FakeQuery(self.rows)

        parsed = {"role": "Java Developer", "skills_text": "java", "salary_text": "", "location": "hybrid"}

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        fake_db = FakeDb([FakeRow(11, "java, spring, spring boot, react")])
        _score, _summary, _src, _e, _r, diag = service.compute_blended_ai_score(
            subject="Java Developer",
            body="Need Java",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resume=None,
            db=fake_db,
            owner_id="default-owner",
            external_thread_id="thread-1",
        )
        self.assertEqual(diag.keyword_source, "thread_carry_forward")
        self.assertTrue(diag.thread_snapshot_used)
        self.assertEqual(diag.thread_snapshot_email_id, 11)

    def test_select_best_resume_match_prefers_hands_on_ai_resume_for_ai_jd(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "AI Engineer",
            "skills_text": "RAG, Tool Calling, Human-in-the-Loop, Agentic Workflows, Embeddings, Observability, Python, Java, TypeScript, REST APIs, Secure SDLC",
            "salary_text": "",
            "location": "Alpharetta, GA",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str, current: bool = False):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = current

        resumes = [
            Resume(
                1,
                "Chaithanya_Dheeraj_Full_Stack_Engineer_Java_Python_GenAI.docx",
                "Java, Python, Prompt Engineering, RAG, Semantic Retrieval, Embeddings, Agentic Workflows, Human-in-the-Loop, AI Evaluations, Observability, Secure SDLC",
                True,
            ),
            Resume(
                2,
                "Chaithanya_Dheeraj_Full_Stack_Java_Engineer_React_AI_Dallas.docx",
                "Java, TypeScript, REST APIs, Generative AI, AI exposure, Agentic AI Concepts, AI-Assisted Engineering, Observability",
            ),
        ]

        selection = service.select_best_resume_match(
            subject="",
            body="Role: AI Engineer. Need RAG, tool calling, embeddings, HITL and observability.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=resumes,
            fallback_resume=resumes[0],
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "Chaithanya_Dheeraj_Full_Stack_Engineer_Java_Python_GenAI.docx")
        self.assertIn("matched_ai_core", selection.ai_summary)

    def test_non_ai_jd_keeps_foundation_bias_stable(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot, Microservices, REST APIs, AWS, CI/CD",
            "salary_text": "",
            "location": "Dallas, TX",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        full_stack = Resume(1, "java_full_stack.docx", "Java, Spring Boot, Microservices, REST APIs, AWS, CI/CD, SQL")
        ai_specialist = Resume(2, "ai_specialist.docx", "RAG, Tool Calling, Embeddings, Prompt Engineering, Python")

        selection = service.select_best_resume_match(
            subject="",
            body="Java Developer role with Spring Boot and AWS.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[full_stack, ai_specialist],
            fallback_resume=full_stack,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "java_full_stack.docx")

    def test_raw_skills_overlap_can_match_unknown_terms_directly(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Workflow Engineer",
            "skills_text": "Temporal Workflow, Java",
            "salary_text": "",
            "location": "Remote",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        temporal_resume = Resume(1, "temporal_resume.docx", "Temporal Workflow, Java, Spring Boot")
        plain_resume = Resume(2, "plain_java_resume.docx", "Java, Spring Boot")

        selection = service.select_best_resume_match(
            subject="",
            body="Need Temporal Workflow and Java experience.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[temporal_resume, plain_resume],
            fallback_resume=temporal_resume,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "temporal_resume.docx")
        self.assertIn("raw_overlap=", selection.ai_summary)
        self.assertIn("matched_raw_skills=temporal workflow, java", selection.ai_summary)

    def test_resume_without_saved_skills_is_scored_weak_without_file_extraction(self) -> None:
        service = ScoringRuntimeService(ScoringRuntimeDeps(generate_embedding_with_health=lambda _text: ([0.1, 0.2], "hash")))
        parsed = {
            "role": "Java Developer",
            "skills_text": "Java, Spring Boot",
            "salary_text": "",
            "location": "Dallas, TX",
        }

        class Settings:
            feature_semantic_enabled = False
            role_keywords = ""
            free_text_guidance = ""

        class Resume:
            def __init__(self, rid: int, name: str, skills: str):
                self.id = rid
                self.file_name = name
                self.skills_text = skills
                self.semantic_embedding = None
                self.file_path = name
                self.is_enabled = True
                self.is_current = False

        weak_resume = Resume(1, "missing_skills.docx", "")
        strong_resume = Resume(2, "strong_skills.docx", "Java, Spring Boot, Microservices")

        selection = service.select_best_resume_match(
            subject="",
            body="Need Java and Spring Boot.",
            parsed=parsed,
            user_settings=Settings(),
            email_row=None,
            resumes=[weak_resume, strong_resume],
            fallback_resume=weak_resume,
        )
        self.assertIsNotNone(selection.resume)
        self.assertEqual(selection.resume.file_name, "strong_skills.docx")
        weak_score, weak_summary = service._intent_weighted_keyword_score(
            parsed=parsed,
            user_settings=Settings(),
            resume=weak_resume,
        )
        self.assertLessEqual(weak_score, 0.18)
        self.assertIn("Resume skills unavailable", weak_summary)


if __name__ == "__main__":
    unittest.main()
