from __future__ import annotations

import html
import re


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
DRAFT_TEXT_SIZE_VALUES = ("small", "normal", "large", "huge")
_DRAFT_TEXT_SIZE_STYLES = {
    "small": "font-size:12px;line-height:1.5;",
    "normal": "font-size:16px;line-height:1.5;",
    "large": "font-size:20px;line-height:1.5;",
    "huge": "font-size:28px;line-height:1.4;",
}


def normalize_draft_text_size(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in DRAFT_TEXT_SIZE_VALUES:
        return normalized
    return "normal"


def _render_inline(text: str) -> str:
    escaped = html.escape(text, quote=True)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def draft_text_to_html(draft_text: str, draft_text_size: str = "normal") -> str:
    text = (draft_text or "").replace("\r\n", "\n").strip()
    size_style = _DRAFT_TEXT_SIZE_STYLES[normalize_draft_text_size(draft_text_size)]
    if not text:
        return f'<div style="{size_style}"><p></p></div>'

    blocks = [block.strip("\n") for block in re.split(r"\n\s*\n", text) if block.strip()]
    rendered_blocks: list[str] = []

    for block in blocks:
        lines = [line.rstrip() for line in block.split("\n")]
        bullet_lines = [line for line in lines if line.lstrip().startswith("- ")]
        all_bullets = len(bullet_lines) == len(lines)

        if all_bullets:
            items: list[str] = []
            for line in lines:
                body = line.lstrip()[2:].strip()
                items.append(f"<li>{_render_inline(body)}</li>")
            rendered_blocks.append(f"<ul>{''.join(items)}</ul>")
            continue

        paragraph_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            paragraph_lines.append(_render_inline(stripped))
        rendered_blocks.append(f"<p>{'<br>'.join(paragraph_lines)}</p>")

    return f'<div style="{size_style}">{"".join(rendered_blocks)}</div>'
