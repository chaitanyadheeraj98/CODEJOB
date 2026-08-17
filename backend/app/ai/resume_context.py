from __future__ import annotations

from app.parsing.document_extraction import extract_document_text


def extract_resume_context(file_path: str, file_name: str, max_chars: int = 7000) -> str:
    return extract_document_text(file_path, file_name, max_chars=max_chars).markdown_text
