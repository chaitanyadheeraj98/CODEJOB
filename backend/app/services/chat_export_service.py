"""Turn a chat session into a document someone can send to a person.

The rendering is not new: `build_docx` and `build_pdf` already take the markdown
subset used here - headings, bullets, bold, pipe tables, rules - and the PDF path
already carries LibreOffice admission control and a timeout that maps to 503. So
this module's whole job is deciding *what a transcript says on paper*, and the
export endpoint reuses the resume pipeline for how it looks.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.models import ChatMessage, ChatSession
from app.services.resume_render_service import ResumeFormatSpec


# A document, not a resume. The resume default is tuned for one dense page; a
# transcript is read like a report, so it gets ordinary margins and a body size
# that survives being printed and passed across a desk.
TRANSCRIPT_SPEC = ResumeFormatSpec(
    page_width_inches=8.5,
    page_height_inches=11.0,
    margin_top_inches=1.0,
    margin_bottom_inches=1.0,
    margin_left_inches=1.0,
    margin_right_inches=1.0,
    body_font_size=10.5,
)

# Markdown control characters at the start of a line. A user who typed "# 1 on
# the shortlist" must not become an H1 in the export, and an assistant reply that
# is already markdown must keep its own structure - so this is applied to the
# authored user text only, never to the assistant's.
_LEADING_MARKUP = re.compile(r"^(\s*)([#>*+-]|\d+\.)\s", re.MULTILINE)


def _quote_user_text(text: str) -> str:
    return _LEADING_MARKUP.sub(lambda m: f"{m.group(1)}\\{m.group(2)} ", text.strip())


def _tool_label(row: ChatMessage) -> str:
    """One line naming the tool, never its payload.

    A tool row carries a whole proposal - the profile card alone can be 20,000
    characters of JSON - and none of it is written for a reader. The document
    records that the step happened and what it was called.
    """
    name = (row.tool_name or "tool").replace("_", " ")
    return f"*Used {name}.*"


def _event_label(row: ChatMessage) -> str:
    # Event rows are already the app's own sentences, written server-side for
    # the transcript. They travel verbatim, italicised as the aside they are.
    return f"*{(row.content or '').strip().strip('[]')}*"


def transcript_markdown(session: ChatSession, messages: list[ChatMessage]) -> str:
    """The session as a readable document.

    Ordered exactly as the thread reads. Tool payloads are named rather than
    dumped, and the assistant's own markdown passes through untouched so its
    headings, tables and lists survive into Word and PDF.
    """
    title = (session.title or f"Chat {session.id}").strip()
    exported = datetime.now(UTC).strftime("%d %B %Y")
    lines: list[str] = [f"# {title}", "", f"CodeJob Assistant · exported {exported}", "", "---", ""]

    for row in messages:
        content = (row.content or "").strip()
        if row.role == "user":
            if not content:
                continue
            lines += ["**You**", "", _quote_user_text(content), ""]
        elif row.role == "assistant":
            if not content:
                continue
            model = getattr(row, "answered_by", None)
            lines += [f"**Assistant** ({model})" if model else "**Assistant**", "", content, ""]
        elif row.role == "event":
            if content:
                lines += [_event_label(row), ""]
        elif row.role == "tool":
            lines += [_tool_label(row), ""]

    # A transcript with nothing in it is still a valid export - the header says
    # which session and when - so this returns a document rather than refusing.
    if len(lines) <= 6:
        lines += ["*This conversation has no messages yet.*", ""]
    return "\n".join(lines).rstrip() + "\n"


def download_name(session: ChatSession, fmt: str) -> str:
    """A filename a person can find again in their downloads folder."""
    raw = (session.title or f"chat-{session.id}").strip()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", raw).strip("-").lower()[:60] or f"chat-{session.id}"
    return f"{slug}.{fmt}"
