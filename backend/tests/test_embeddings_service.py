import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.config import settings
from app.semantic.embeddings_service import generate_embedding


class EmbeddingsServiceTests(unittest.TestCase):
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

    def test_sbert_provider_path_works(self) -> None:
        with self._settings(
            semantic_embedding_provider="sbert",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([0.11, 0.22], "sbert")):
                vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [0.11, 0.22])

    def test_sbert_failure_falls_back_to_hash(self) -> None:
        with self._settings(
            semantic_embedding_provider="sbert",
            semantic_embedding_dimension=64,
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", side_effect=ValueError("No embedding data received")):
                vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "hash")
        self.assertEqual(len(vector), 64)

    def test_hash_provider_path_works(self) -> None:
        with self._settings(
            semantic_embedding_provider="hash",
            semantic_embedding_dimension=64,
            semantic_embedding_latency_log_enabled=False,
        ):
            vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "hash")
        self.assertEqual(len(vector), 64)

    def test_legacy_gemini_provider_normalizes_to_sbert(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([0.7, 0.6], "sbert")):
                vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [0.7, 0.6])

    def test_legacy_openrouter_provider_normalizes_to_sbert(self) -> None:
        with self._settings(
            semantic_embedding_provider="openrouter",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([1.0, 2.0], "sbert")):
                vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [1.0, 2.0])

    def test_legacy_google_embedding_provider_alias_normalizes_to_sbert(self) -> None:
        with self._settings(
            semantic_embedding_provider="",
            google_embedding_provider="gemini",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([0.3, 0.4], "sbert")):
                vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [0.3, 0.4])

    def test_latency_log_uses_actual_sbert_model_name(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=True,
        ):
            with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([0.5, 0.6], "sbert")):
                with patch("app.semantic.embeddings_service.logger.warning") as warning:
                    vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [0.5, 0.6])
        self.assertGreaterEqual(warning.call_count, 1)
        latency_call = warning.call_args_list[-1]
        self.assertEqual(latency_call.args[1], "sbert")
        self.assertEqual(latency_call.args[2], "sentence-transformers/all-MiniLM-L6-v2")


if __name__ == "__main__":
    unittest.main()
