import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.config import settings
from app.semantic.embeddings_service import generate_embedding


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


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

    def test_gemini_success_returns_vector(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            google_embedding_api_key="test-key",
            google_embedding_model="gemini-embedding-2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service.request.urlopen", return_value=_FakeResponse({"embedding": {"values": [0.1, 0.2]}})):
                vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "gemini")
        self.assertEqual(vector, [0.1, 0.2])

    def test_gemini_falls_back_to_openrouter(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            google_embedding_api_key="test-key",
            semantic_embedding_fallback_provider="openrouter",
            semantic_embedding_fallback_model="openai/text-embedding-3-small",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._gemini_embedding", side_effect=ValueError("No embedding data received")):
                with patch("app.semantic.embeddings_service._openrouter_embedding", return_value=([0.9, 0.8], "openrouter")):
                    vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "openrouter")
        self.assertEqual(vector, [0.9, 0.8])

    def test_gemini_and_openrouter_fall_back_to_hash(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            google_embedding_api_key="test-key",
            semantic_embedding_fallback_provider="openrouter",
            semantic_embedding_dimension=64,
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._gemini_embedding", side_effect=ValueError("No embedding data received")):
                with patch("app.semantic.embeddings_service._openrouter_embedding", side_effect=ValueError("No embedding data received")):
                    with patch("app.semantic.embeddings_service._sbert_embedding", side_effect=ValueError("No embedding data received")):
                        vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "hash")
        self.assertEqual(len(vector), 64)

    def test_gemini_and_openrouter_fall_back_to_sbert(self) -> None:
        with self._settings(
            semantic_embedding_provider="gemini",
            semantic_embedding_model="gemini-embedding-2",
            google_embedding_api_key="test-key",
            semantic_embedding_fallback_provider="openrouter",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._gemini_embedding", side_effect=ValueError("No embedding data received")):
                with patch("app.semantic.embeddings_service._openrouter_embedding", side_effect=ValueError("No embedding data received")):
                    with patch("app.semantic.embeddings_service._sbert_embedding", return_value=([0.7, 0.6], "sbert")):
                        vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "sbert")
        self.assertEqual(vector, [0.7, 0.6])

    def test_openrouter_falls_back_to_sbert_then_hash(self) -> None:
        with self._settings(
            semantic_embedding_provider="openrouter",
            semantic_embedding_model="openai/text-embedding-3-small",
            semantic_embedding_dimension=64,
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._openrouter_embedding", side_effect=ValueError("No embedding data received")):
                with patch("app.semantic.embeddings_service._sbert_embedding", side_effect=ValueError("No embedding data received")):
                    vector, provider = generate_embedding("hello world")
        self.assertEqual(provider, "hash")
        self.assertEqual(len(vector), 64)

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

    def test_openrouter_provider_path_unchanged(self) -> None:
        with self._settings(
            semantic_embedding_provider="openrouter",
            semantic_embedding_model="openai/text-embedding-3-small",
            semantic_embedding_sbert_model="sentence-transformers/all-MiniLM-L6-v2",
            semantic_embedding_latency_log_enabled=False,
        ):
            with patch("app.semantic.embeddings_service._openrouter_embedding", return_value=([1.0, 2.0], "openrouter")):
                vector, provider = generate_embedding("hello")
        self.assertEqual(provider, "openrouter")
        self.assertEqual(vector, [1.0, 2.0])


if __name__ == "__main__":
    unittest.main()
