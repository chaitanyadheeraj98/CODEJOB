"""C4: keep torch out of the request-serving processes.

The checkpoint said "move SBERT to the RQ worker, or memory x4". The concern
was right and the cause was not SBERT. A freshly restarted API process that has
served nothing but /health already has torch mapped, because
`langchain_core/language_models/base.py` does:

    try:
        from transformers import GPT2TokenizerFast
        _HAS_TRANSFORMERS = True
    except ImportError:
        _HAS_TRANSFORMERS = False

It wants a GPT-2 tokenizer for counting tokens and degrades cleanly without
one - but `transformers` imports torch, which is 483 MB, loaded at start-up
purely because the package is installed for SBERT's benefit.

Measured on the running image: `import app.main` is **689 MB with torch** and
**203 MB without** it.
"""

import os
import sys
import unittest
from importlib.abc import MetaPathFinder
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.config import settings
from app import torch_guard
from app.semantic import embeddings_service
from app.semantic.embeddings_service import EmbeddingNotAvailableHere


class GuardInstallationTests(unittest.TestCase):
    def tearDown(self):
        sys.meta_path[:] = [
            finder for finder in sys.meta_path
            if not isinstance(finder, torch_guard._DeclineTransformers)
        ]

    def test_it_stays_out_of_the_way_by_default(self):
        """Default is today's behaviour: nothing is declined."""
        with patch.object(settings, "api_embeddings_enabled", True):
            self.assertFalse(torch_guard.install())

    def test_the_worker_is_never_guarded(self):
        """The worker is where the embeddings run."""
        with patch.object(settings, "process_role", "worker"), \
                patch.object(settings, "api_embeddings_enabled", False):
            self.assertFalse(torch_guard.install())

    def test_it_installs_when_the_api_may_not_embed(self):
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.dict(sys.modules):
            sys.modules.pop("transformers", None)
            self.assertTrue(torch_guard.install())
            self.assertTrue(
                any(isinstance(f, torch_guard._DeclineTransformers) for f in sys.meta_path)
            )

    def test_it_declines_rather_than_pretending(self):
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.dict(sys.modules):
            sys.modules.pop("transformers", None)
            torch_guard.install()
            with self.assertRaises(ImportError):
                import transformers  # noqa: F401

    def test_an_unrelated_import_is_untouched(self):
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.dict(sys.modules):
            sys.modules.pop("transformers", None)
            torch_guard.install()
            import json  # noqa: F401

    def test_it_says_so_when_it_is_too_late_to_help(self):
        """Silence here would hide a 486 MB difference that did not happen."""
        import logging

        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.dict(sys.modules, {"transformers": object()}):
            logging.disable(logging.NOTSET)
            with self.assertLogs("app.torch_guard", "WARNING") as logs:
                self.assertFalse(torch_guard.install())
        self.assertIn("already_imported", "\n".join(logs.output))

    def test_the_hook_defines_the_name_python_actually_calls(self):
        """`find_module` was removed in 3.12.

        A hook defining only the old name installs happily and is never
        consulted - it looks like it works and silently does nothing. That is
        exactly what happened during the first measurement of this, which
        reported a saving of 57 MB instead of 486 MB.
        """
        self.assertTrue(hasattr(torch_guard._DeclineTransformers, "find_spec"))
        self.assertTrue(issubclass(torch_guard._DeclineTransformers, MetaPathFinder))


class EmbeddingRefusalTests(unittest.TestCase):
    """Refusing is the point; falling back quietly would not be."""

    def test_the_api_refuses_when_embeddings_are_disabled_there(self):
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.object(settings, "process_role", "api"):
            with self.assertRaises(EmbeddingNotAvailableHere):
                embeddings_service._embeddings_allowed_here()

    def test_the_worker_never_refuses(self):
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.object(settings, "process_role", "worker"):
            self.assertIsNone(embeddings_service._embeddings_allowed_here())

    def test_nothing_changes_while_the_switch_is_on(self):
        with patch.object(settings, "api_embeddings_enabled", True):
            self.assertIsNone(embeddings_service._embeddings_allowed_here())

    def test_it_raises_rather_than_falling_back_to_hash(self):
        """Hash and SBERT give different answers.

        A scoring path that silently changed provider would be a correctness
        change wearing a memory optimisation's clothes.
        """
        with patch.object(settings, "api_embeddings_enabled", False), \
                patch.object(settings, "process_role", "api"):
            with self.assertRaises(EmbeddingNotAvailableHere):
                embeddings_service._load_sbert_model("any-model", "cpu")


if __name__ == "__main__":
    unittest.main()
