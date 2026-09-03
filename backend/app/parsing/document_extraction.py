from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from unstructured.documents.elements import Element


logger = logging.getLogger(__name__)

_DROPPED_CATEGORIES = {"Footer", "Header", "Page-footer", "Page-header", "PageBreak", "PageNumber"}
_HTML_MARKUP_PATTERN = re.compile(
    r"(?is)<!doctype\s+html\b|<(?:html|head|body|style|script|table|tbody|thead|tr|td|th|div|span|p|br|ul|ol|li|h[1-6]|a|blockquote|section|article|font)\b"
)
_GMAIL_QUOTE_HEADER_PATTERN = re.compile(r"^on\s+.+\s+wrote:\s*$", re.IGNORECASE)
_GMAIL_QUOTE_PREFIX_PATTERN = re.compile(r"^\s*>+\s?")
_GOOGLE_GROUPS_FOOTER = "you received this message because you are subscribed to the google groups"
_SIGNOFF_PATTERN = re.compile(
    r"^(?:thanks?(?:\s*(?:&|and)\s*)?regards?|warm regards|best regards|kind regards|regards|sincerely)[,!?.]*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedDocument:
    markdown_text: str
    plain_text: str
    elements: list[Element]
    truncated: bool


def _content_elements(elements: list[Element]) -> list[Element]:
    return [
        element
        for element in elements
        if element.category not in _DROPPED_CATEGORIES and str(getattr(element, "text", "") or "").strip()
    ]


def _table_to_markdown(element: Element) -> str:
    table_html = str(getattr(element.metadata, "text_as_html", "") or "").strip()
    if not table_html:
        return element.text.strip()

    rows: list[list[str]] = []
    header_row = False
    for row_index, row in enumerate(BeautifulSoup(table_html, "html.parser").find_all("tr")):
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        if row_index == 0:
            header_row = any(cell.name == "th" for cell in cells)
        rows.append([" ".join(cell.get_text(" ", strip=True).split()).replace("|", r"\|") for cell in cells])

    if not rows:
        return element.text.strip()

    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    if header_row:
        header, body = normalized[0], normalized[1:]
    else:
        header, body = [f"Column {index}" for index in range(1, width + 1)], normalized
    rendered = [
        f"| {' | '.join(header)} |",
        f"| {' | '.join('---' for _ in range(width))} |",
    ]
    rendered.extend(f"| {' | '.join(row)} |" for row in body)
    return "\n".join(rendered)


def elements_to_markdown(elements: list[Element]) -> str:
    blocks: list[str] = []
    for element in _content_elements(elements):
        text = element.text.strip()
        category = element.category
        if category == "Title":
            depth = max(0, int(getattr(element.metadata, "category_depth", 0) or 0))
            block = f"{'#' * min(6, depth + 2)} {text}"
        elif category == "ListItem":
            block = "\n".join(f"- {line.strip()}" for line in text.splitlines() if line.strip())
        elif category in {"Table", "TableChunk"}:
            block = _table_to_markdown(element)
        else:
            block = text
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def _elements_to_plain_text(elements: list[Element]) -> str:
    return "\n\n".join(element.text.strip() for element in _content_elements(elements)).strip()


def _clip_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 0:
        return ""
    clipped = text[:max_chars].rstrip()
    boundary = max(clipped.rfind("\n\n"), clipped.rfind("\n"), clipped.rfind(" "))
    return clipped[:boundary].rstrip() if boundary >= max_chars * 0.8 else clipped


def _render_with_limit(elements: list[Element], max_chars: int | None) -> tuple[str, str, bool]:
    content = _content_elements(elements)
    markdown = elements_to_markdown(content)
    plain = _elements_to_plain_text(content)
    if max_chars is None or len(markdown) <= max_chars:
        return markdown, plain, False
    if max_chars <= 0:
        return "", "", bool(markdown)

    from unstructured.chunking.title import chunk_by_title

    chunks = chunk_by_title(
        content,
        combine_text_under_n_chars=0,
        include_orig_elements=True,
        max_characters=max_chars,
    )
    markdown_chunks: list[str] = []
    plain_chunks: list[str] = []
    for chunk in chunks:
        original_elements = list(getattr(chunk.metadata, "orig_elements", None) or [chunk])
        chunk_markdown = elements_to_markdown(original_elements)
        chunk_plain = _elements_to_plain_text(original_elements)
        candidate = "\n\n".join([*markdown_chunks, chunk_markdown]).strip()
        if chunk_markdown and len(candidate) <= max_chars:
            markdown_chunks.append(chunk_markdown)
            plain_chunks.append(chunk_plain)
            continue
        if not markdown_chunks:
            markdown_chunks.append(_clip_text(chunk_markdown or chunk.text, max_chars))
            plain_chunks.append(_clip_text(chunk_plain or chunk.text, max_chars))
        break

    return "\n\n".join(markdown_chunks).strip(), "\n\n".join(plain_chunks).strip(), True


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _legacy_pdf_text(file_path: Path) -> str:
    try:
        from pypdf import PdfReader

        return _compact(" ".join((page.extract_text() or "") for page in PdfReader(str(file_path)).pages))
    except Exception:
        return ""


def _legacy_docx_text(file_path: Path) -> str:
    try:
        with zipfile.ZipFile(file_path) as docx_zip:
            xml_data = docx_zip.read("word/document.xml")
        root = ElementTree.fromstring(xml_data)
    except Exception:
        return ""
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    return _compact(" ".join(node.text or "" for node in root.findall(".//w:t", namespace)))


def _fallback_document(path: Path, file_name: str, max_chars: int, label: str) -> ExtractedDocument:
    if not path.exists():
        message = f"{label} '{file_name}' was not found on disk."
        return ExtractedDocument(message, message, [], False)

    extracted = _legacy_pdf_text(path) if path.suffix.lower() == ".pdf" else ""
    if path.suffix.lower() == ".docx":
        extracted = _legacy_docx_text(path)
    if not extracted:
        message = (
            f"{label} available as '{file_name}', but text extraction is limited. "
            "Use only conservative claims and keep the reply concise."
        )
        return ExtractedDocument(message, message, [], False)

    clipped = _clip_text(extracted, max_chars)
    return ExtractedDocument(clipped, clipped, [], len(clipped) < len(extracted))


def extract_document_text(
    file_path: str,
    file_name: str,
    *,
    max_chars: int = 7000,
    label: str = "Resume file",
) -> ExtractedDocument:
    """Extract a document to markdown.

    `label` names the thing in the fallback messages, which are handed straight
    to the model. It defaults to the resume wording this function was written
    for; a chat attachment is often a job description, and telling the model a
    JD is a resume is worse than telling it nothing.
    """
    path = Path(file_path)
    if not path.exists():
        return _fallback_document(path, file_name, max_chars, label)

    try:
        from unstructured.partition.auto import partition

        elements = list(partition(filename=str(path), strategy="fast"))
        markdown, plain, truncated = _render_with_limit(elements, max_chars)
        if markdown:
            return ExtractedDocument(markdown, plain, elements, truncated)
    except Exception:
        pass
    return _fallback_document(path, file_name, max_chars, label)


def _legacy_strip_html(html: str) -> str:
    no_scripts = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    line_broken = re.sub(r"(?is)<(br\s*/?|/p|/div|/li|/tr|/h[1-6])\s*>", "\n", no_scripts)
    no_tags = re.sub(r"(?is)<[^>]+>", " ", line_broken)
    compact = re.sub(r"[ \t]+", " ", no_tags)
    return re.sub(r"\n\s*\n+", "\n\n", compact).strip()


_BR_TAG_PATTERN = re.compile(r"(?is)<br\s*/?>")


def _normalize_line_breaks_for_partitioning(html: str) -> str:
    # unstructured's partition_html only splits into separate elements at block-container
    # boundaries (<div>, <p>, ...) -- a run of text separated only by <br> inside a single
    # container (very common: Gmail/Outlook compose, mailing-list relays) comes back as one
    # element with <br> collapsed to a space, and elements_to_markdown then has nothing to
    # join on. Splitting on <br> into sibling <div>s gives partition_html a boundary to segment.
    return _BR_TAG_PATTERN.sub("</div><div>", html)


def clean_html_text(html: str, *, max_chars: int | None = None) -> str:
    if not html.strip():
        return ""
    try:
        from unstructured.partition.html import partition_html

        elements = list(partition_html(text=_normalize_line_breaks_for_partitioning(html)))
        markdown, _plain, _truncated = _render_with_limit(elements, max_chars)
        if markdown:
            return markdown
    except Exception:
        logger.warning("clean_html_text partition_html failed, using legacy fallback", exc_info=True)
    fallback = _legacy_strip_html(html)
    return _clip_text(fallback, max_chars) if max_chars is not None else fallback


def clean_html_if_present(text: str, *, max_chars: int | None = None) -> str:
    if not text.strip() or not _HTML_MARKUP_PATTERN.search(text):
        return text
    return clean_html_text(text, max_chars=max_chars)


def _email_line_text(line: str) -> str:
    return _GMAIL_QUOTE_PREFIX_PATTERN.sub("", line).strip().strip("*_ ")


def _gmail_quote_header_length(lines: list[str], start: int) -> int:
    for length in (1, 2):
        if start + length > len(lines):
            continue
        candidate = " ".join(_email_line_text(line) for line in lines[start : start + length])
        if _GMAIL_QUOTE_HEADER_PATTERN.fullmatch(" ".join(candidate.split())):
            return length
    return 0


def _strip_google_groups_footers(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        if _GOOGLE_GROUPS_FOOTER not in _email_line_text(lines[index]).casefold():
            cleaned.append(lines[index])
            index += 1
            continue

        previous = len(cleaned) - 1
        while previous >= 0 and not _email_line_text(cleaned[previous]):
            previous -= 1
        if previous >= 0 and _email_line_text(cleaned[previous]) == "--":
            del cleaned[previous:]

        index += 1
        while index < len(lines) and _email_line_text(lines[index]):
            index += 1
        while index < len(lines) and not _email_line_text(lines[index]):
            index += 1
    return cleaned


def _strip_trailing_signature(lines: list[str]) -> list[str]:
    while lines and not _email_line_text(lines[-1]):
        lines.pop()
    for index in range(len(lines) - 1, -1, -1):
        line = _email_line_text(lines[index])
        if line != "--" and not _SIGNOFF_PATTERN.fullmatch(line):
            continue
        tail = [item for item in lines[index + 1 :] if _email_line_text(item)]
        if len(tail) <= 20 and sum(len(_email_line_text(item)) for item in tail) <= 1200:
            return lines[:index]
    return lines


def _clean_email_segment(lines: list[str], *, strip_signature: bool = True) -> list[str]:
    lines = _strip_google_groups_footers(lines)
    return _strip_trailing_signature(lines) if strip_signature else lines


def strip_gmail_boilerplate(text: str) -> str:
    """Remove deterministic Gmail thread noise while preserving substantive reply text."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    marker_start = -1
    marker_length = 0
    for index in range(len(lines)):
        marker_length = _gmail_quote_header_length(lines, index)
        if marker_length:
            marker_start = index
            break

    if marker_start >= 0:
        reply = _clean_email_segment(lines[:marker_start])
        quoted = [
            _GMAIL_QUOTE_PREFIX_PATTERN.sub("", line)
            for line in lines[marker_start + marker_length :]
        ]
        cleaned_lines = [*reply, "", *_clean_email_segment(quoted)]
    else:
        cleaned_lines = _clean_email_segment(lines)

    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return cleaned or text.strip()


def extract_gmail_reply_body(text: str, *, strip_signature: bool = True) -> str:
    """Return only the newest Gmail reply, without quoted thread history.

    strip_signature=False keeps the sender's own trailing signature block - a phone
    number extractor needs it, since that's exactly where a recruiter's number lives.
    """
    source = clean_html_if_present(text)
    lines = source.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    marker_start = next(
        (index for index in range(len(lines)) if _gmail_quote_header_length(lines, index)),
        len(lines),
    )
    current = lines[:marker_start]
    cleaned_lines = _clean_email_segment(current, strip_signature=strip_signature)
    if not any(_email_line_text(line) for line in cleaned_lines):
        cleaned_lines = _strip_google_groups_footers(current)
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned_lines)).strip()
    return cleaned or source.strip()


def prepare_gmail_parse_body(text: str, *, max_chars: int | None = None) -> str:
    """Build parser input from a Gmail body without changing the persisted source."""
    return strip_gmail_boilerplate(clean_html_if_present(text, max_chars=max_chars))
