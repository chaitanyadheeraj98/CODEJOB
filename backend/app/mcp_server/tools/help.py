from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

_DOC_PATH = Path(__file__).resolve().parent / "app_help.md"


# ponytail: cached per-process; edits to app_help.md need a backend restart to show up.
@lru_cache(maxsize=1)
def _sections() -> dict[str, str]:
    """Parse app_help.md into {heading: body}, split on '## ' headings."""
    text = _DOC_PATH.read_text(encoding="utf-8")
    sections: dict[str, str] = {}
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        heading, _, body = block.partition("\n")
        sections[heading.strip()] = body.strip()
    return sections


def get_app_help(topic: str = "") -> dict[str, object]:
    """Look up a short how-to for a known CodeJob workflow (e.g. "resume upload").

    Call with an empty topic to list all available topics.
    """
    sections = _sections()
    query = topic.strip().lower()
    if not query:
        return {"topics": list(sections)}

    for heading, body in sections.items():
        if query in heading.lower() or heading.lower() in query:
            return {"topic": heading, "help": body}

    match = difflib.get_close_matches(query, [h.lower() for h in sections], n=1, cutoff=0.6)
    if match:
        heading = next(h for h in sections if h.lower() == match[0])
        return {"topic": heading, "help": sections[heading]}

    return {"error": f"No help topic matches '{topic}'.", "topics": list(sections)}
