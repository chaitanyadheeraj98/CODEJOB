from __future__ import annotations


def sanitize_plain_text(text: str) -> str:
    cleaned_lines = [raw.strip() for raw in text.splitlines()]
    return "\n".join(cleaned_lines).strip()


def extract_subject_line(text: str) -> str | None:
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("subject:"):
            return stripped
    return None
