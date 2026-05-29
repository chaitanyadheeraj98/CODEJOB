import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
import os

os.environ["DEBUG"] = "false"

from app.config import Settings


class SettingsEmbeddingEnvCompatTests(unittest.TestCase):
    def _load_from_env_text(self, env_text: str) -> Settings:
        with TemporaryDirectory() as td:
            env_file = Path(td) / ".env"
            env_file.write_text(env_text, encoding="utf-8")
            return Settings(_env_file=str(env_file))

    def test_legacy_googleembedding_provider_alias_does_not_crash(self) -> None:
        cfg = self._load_from_env_text(
            "\n".join(
                [
                    "GoogleEmbedding_PROVIDER=gemini",
                    "GoogleEmbedding_API_KEY=test-key",
                ]
            )
        )
        self.assertEqual(cfg.google_embedding_provider, "gemini")
        self.assertEqual(cfg.effective_semantic_embedding_provider, "gemini")

    def test_semantic_embedding_provider_takes_precedence(self) -> None:
        cfg = self._load_from_env_text(
            "\n".join(
                [
                    "SEMANTIC_EMBEDDING_PROVIDER=openrouter",
                    "GoogleEmbedding_PROVIDER=gemini",
                ]
            )
        )
        self.assertEqual(cfg.effective_semantic_embedding_provider, "openrouter")

    def test_uppercase_legacy_provider_alias_is_accepted(self) -> None:
        cfg = self._load_from_env_text("GOOGLEEMBEDDING_PROVIDER=gemini")
        self.assertEqual(cfg.effective_semantic_embedding_provider, "gemini")


if __name__ == "__main__":
    unittest.main()
