from __future__ import annotations

from html import escape as html_escape
from html.parser import HTMLParser
import re
import json


def escape(value: object) -> str:
    return html_escape(str(value), quote=True)


class _PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _PlainTextParser()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _split_text(text: str, limit: int) -> tuple[str, str]:
    window = text[: limit + 1]
    for separator in ("\n\n", "\n"):
        split_at = window.rfind(separator, 0, limit + 1)
        if split_at > 0:
            end = split_at + len(separator)
            return text[:end], text[end:]
    return text[:limit], text[limit:]


def chunk(text: str, limit: int = 4096) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if not text:
        return []

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        pre_start = remaining.rfind("<pre>", 0, limit + 1)
        pre_end = remaining.rfind("</pre>", 0, limit + 1)
        if pre_start > pre_end:
            before = remaining[:pre_start]
            if before:
                part, unused = _split_text(before, limit)
                chunks.append(part)
                remaining = unused + remaining[pre_start:]
                continue
            closing = "</pre>"
            opening = "<pre>"
            body_limit = limit - len(opening) - len(closing)
            body, rest = _split_text(remaining[len(opening) :], body_limit)
            chunks.append(opening + body + closing)
            remaining = opening + rest
            continue
        part, remaining = _split_text(remaining, limit)
        chunks.append(part)
    if remaining:
        chunks.append(remaining)
    return chunks


def format_answer(text: str) -> str:
    sections = text.split("```")
    rendered: list[str] = []
    for index, section in enumerate(sections):
        safe = html_escape(section, quote=True)
        if index % 2:
            rendered.append(f"<pre>{safe}</pre>")
            continue
        safe = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", safe, flags=re.DOTALL)
        safe = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", safe)
        rendered.append(safe)
    return "".join(rendered)


def email_proposal(content: str, message_id: int) -> tuple[str, list[list[dict[str, str]]] | None] | None:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") == "missing_fields":
        missing = payload.get("missing")
        if not isinstance(missing, list) or not all(isinstance(value, str) and value for value in missing):
            return None
        return f"I need {escape(', '.join(missing))} before I can prepare that email.", None
    required = ("candidate_email_id", "to", "subject", "body")
    if payload.get("action") != "send_email" or any(not payload.get(key) for key in required):
        return None
    if not isinstance(payload["candidate_email_id"], int) or payload["candidate_email_id"] <= 0:
        return None
    document_ids = payload.get("document_ids", [])
    document_names = payload.get("document_names", [])
    if (
        not isinstance(document_ids, list)
        or not isinstance(document_names, list)
        or len(document_ids) != len(document_names)
        or not all(isinstance(value, int) and value > 0 for value in document_ids)
        or not all(isinstance(value, str) and value.strip() for value in document_names)
    ):
        return None
    body = str(payload["body"])
    preview = body if len(body) <= 1500 else body[:1497].rstrip() + "..."
    lines = [
        "<b>Email ready to send</b>",
        f"<b>To:</b> {escape(payload['to'])}",
        f"<b>Subject:</b> {escape(payload['subject'])}",
    ]
    cc = str(payload.get("cc") or "").strip()
    if cc:
        lines.append(f"<b>CC:</b> {escape(cc)}")
    if document_names:
        lines.append("<b>Attachments:</b> " + ", ".join(escape(name) for name in document_names))
    lines.extend(("<b>Body preview:</b>", f"<pre>{escape(preview)}</pre>"))
    keyboard = [[
        {"text": "Send", "callback_data": f"act:prop:send:{message_id}"},
        {"text": "Cancel", "callback_data": f"act:prop:cancel:{message_id}"},
    ]]
    assert all(len(button["callback_data"].encode()) <= 64 for row in keyboard for button in row)
    return "\n".join(lines), keyboard
