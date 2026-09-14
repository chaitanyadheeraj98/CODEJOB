"""Decline the `transformers` import in processes that must not embed.

`langchain_core/language_models/base.py` does:

    try:
        from transformers import GPT2TokenizerFast
        _HAS_TRANSFORMERS = True
    except ImportError:
        _HAS_TRANSFORMERS = False

It wants a GPT-2 tokenizer to count tokens, and it degrades cleanly without
one. But `transformers` imports `torch`, and torch is **483 MB** - loaded at
application start, before a single request, purely because the package happens
to be installed for SBERT's benefit.

Measured on the running image:

    import app.main, as shipped        746 MB, torch mapped
    import app.main, transformers gone 204 MB, torch absent

That is the difference between `uvicorn --workers 4` costing about 3 GB before
serving anything and costing about 0.8 GB.

Installed only where it is safe
-------------------------------
Declining the import makes any real use of `transformers` fail rather than be
slow, so it is tied to the same switch that forbids embedding in this process.
The worker never installs it: the worker is where the embeddings run.

Import order matters
--------------------
This has to run before anything imports `langchain_core`, because the flag it
sets is read once at *that* module's import. `app/main.py` imports it first,
ahead of the chat router.
"""

from __future__ import annotations

import logging
import sys
from importlib.abc import MetaPathFinder

from app.config import settings

logger = logging.getLogger(__name__)

BLOCKED_ROOTS = ("transformers",)


class _DeclineTransformers(MetaPathFinder):
    """Report `transformers` as absent, which is what langchain_core asks."""

    def find_spec(self, fullname, path=None, target=None):
        # `find_spec`, not `find_module`: Python 3.12 removed the latter, and a
        # hook defining only the old name is never called - it looks installed
        # and silently does nothing.
        if fullname.split(".")[0] in BLOCKED_ROOTS:
            raise ImportError(
                f"{fullname} is not available in this process: it pulls in torch, "
                "and embeddings belong to the worker. See app/torch_guard.py."
            )
        return None


def install() -> bool:
    """Decline the import if this process is forbidden from embedding.

    Returns whether the guard was installed, so start-up can log the fact
    rather than leaving a 540 MB difference invisible.
    """
    if settings.process_role == "worker" or settings.api_embeddings_enabled:
        return False
    if "transformers" in sys.modules:
        # Too late to help, and pretending otherwise would be worse than saying
        # so: something imported it before this ran.
        logger.warning("torch_guard_late transformers_already_imported=1")
        return False
    sys.meta_path.insert(0, _DeclineTransformers())
    logger.info("torch_guard_installed reason=api_embeddings_disabled")
    return True
