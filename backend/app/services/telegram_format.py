from __future__ import annotations

from html import escape as html_escape
from html.parser import HTMLParser


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
