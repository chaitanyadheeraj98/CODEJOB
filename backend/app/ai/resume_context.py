from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def _compact(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_pdf_text(file_path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]
    except Exception:
        return ""
    try:
        reader = PdfReader(str(file_path))
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        return _compact(" ".join(chunks))
    except Exception:
        return ""


def _extract_docx_text(file_path: Path) -> str:
    try:
        with zipfile.ZipFile(file_path) as docx_zip:
            xml_data = docx_zip.read("word/document.xml")
    except Exception:
        return ""
    try:
        root = ElementTree.fromstring(xml_data)
    except Exception:
        return ""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    texts = [node.text or "" for node in root.findall(".//w:t", ns)]
    return _compact(" ".join(texts))


def extract_resume_context(file_path: str, file_name: str, max_chars: int = 7000) -> str:
    path = Path(file_path)
    if not path.exists():
        return f"Resume file '{file_name}' was not found on disk."

    suffix = path.suffix.lower()
    extracted = ""
    if suffix == ".pdf":
        extracted = _extract_pdf_text(path)
    elif suffix == ".docx":
        extracted = _extract_docx_text(path)
    elif suffix == ".doc":
        extracted = ""

    if not extracted:
        return (
            f"Resume available as '{file_name}', but text extraction is limited. "
            "Use only conservative claims and keep the reply concise."
        )
    return extracted[:max_chars]

