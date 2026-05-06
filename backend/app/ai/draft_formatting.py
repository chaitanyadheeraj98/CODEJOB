from __future__ import annotations

import html
import re


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _render_inline(text: str) -> str:
    escaped = html.escape(text, quote=True)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def draft_text_to_html(draft_text: str) -> str:
    text = (draft_text or "").replace("\r\n", "\n").strip()
    if not text:
        return "<p></p>"

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

    return "".join(rendered_blocks)
