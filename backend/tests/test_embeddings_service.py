import os
import sys
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.config import settings
from app.semantic import embeddings_service
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

    def test_sbert_model_load_passes_hf_token(self) -> None:
        previous_instance = embeddings_service._sbert_model_instance
        previous_name = embeddings_service._sbert_model_name
        previous_device = embeddings_service._sbert_model_device
        embeddings_service._sbert_model_instance = None
        embeddings_service._sbert_model_name = ""
        embeddings_service._sbert_model_device = ""
        try:
            with self._settings(
                hf_token="hf_test_token",
                semantic_embedding_sbert_device="cpu",
            ):
                fake_constructor = SimpleNamespace()
                fake_module = SimpleNamespace(SentenceTransformer=lambda *args, **kwargs: fake_constructor)
                with patch.dict(sys.modules, {"sentence_transformers": fake_module}):
                    with patch.object(fake_module, "SentenceTransformer", return_value=fake_constructor) as constructor:
                        model = embeddings_service._load_sbert_model("sentence-transformers/all-MiniLM-L6-v2", "cpu")
            self.assertIsNotNone(model)
            constructor.assert_called_once_with(
                "sentence-transformers/all-MiniLM-L6-v2",
                device="cpu",
                token="hf_test_token",
            )
        finally:
            embeddings_service._sbert_model_instance = previous_instance
            embeddings_service._sbert_model_name = previous_name
            embeddings_service._sbert_model_device = previous_device


if __name__ == "__main__":
    unittest.main()
