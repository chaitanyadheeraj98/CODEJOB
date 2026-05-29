import unittest

from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService


class ScoringRuntimeServiceTests(unittest.TestCase):
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

    def test_neutral_semantic_fallback_not_keyword_only_drop(self) -> None:
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
        self.assertEqual(source, "v2_rules_plus_semantic_neutral_fallback")
        self.assertIn("neutral semantic score applied", summary)
        self.assertGreater(score, 0.57)
        self.assertIsNotNone(diag.fallback_reason)

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


if __name__ == "__main__":
    unittest.main()
