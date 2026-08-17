import os
import unittest
from contextlib import contextmanager

os.environ["DEBUG"] = "false"

from app import main
from app.config import settings


class AIStatusEmbeddingTests(unittest.TestCase):
    @contextmanager
    def _settings(self, **overrides):
        old = {}
        for key, value in overrides.items():
            old[key] = getattr(settings, key)
            setattr(settings, key, value)
        try:
            yield
        finally:
            for key, value in old.items():
                setattr(settings, key, value)

    def test_ai_status_legacy_gemini_config_uses_local_sbert_runtime(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            google_embedding_api_key="test-key",
            semantic_embedding_fallback_provider="openrouter",
            semantic_embedding_fallback_model="openai/text-embedding-3-small",
        ):
            resp = main.ai_status()
        self.assertEqual(resp.embedding_provider, "sbert")
        self.assertTrue(resp.embedding_configured)
        self.assertEqual(resp.embedding_model, "sentence-transformers/all-MiniLM-L6-v2")
        self.assertIn("local sbert primary", resp.embedding_detail)
        self.assertIn("fallback=hash", resp.embedding_detail)
        self.assertNotIn("openrouter", resp.embedding_detail.lower())
        self.assertNotIn("gemini", resp.embedding_detail.lower())

    def test_ai_status_hash_reports_hash_only_runtime(self) -> None:
        with self._settings(
            semantic_embedding_provider="hash",
            semantic_embedding_dimension=384,
        ):
            resp = main.ai_status()
        self.assertEqual(resp.embedding_provider, "hash")
        self.assertEqual(resp.embedding_model, "hash:384")
        self.assertTrue(resp.embedding_configured)
        self.assertIn("local hash embeddings", resp.embedding_detail.lower())


if __name__ == "__main__":
    unittest.main()
