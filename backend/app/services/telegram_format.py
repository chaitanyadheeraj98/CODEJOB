from __future__ import annotations

from html import escape as html_escape
from html.parser import HTMLParser
import re
import json

from app.services.proposal_actions import PROPOSAL_ACTIONS


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


# The tools whose rows `email_proposal` can draw. One name lived in three
# places - the renderer, the worker that decides what to render, and the
# callback that reloads a card - and the worker was missed when composing was
# added, so a composed email produced a promise of a card and no card.
EMAIL_PROPOSAL_TOOLS = frozenset({"propose_send_email", "propose_new_email"})


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
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        # The app contradicting the model, in the app's own words. Without this
        # a refused tool call renders nothing here, the assistant says the card
        # is ready, and the only thing that knows better stays silent - the web
        # front end has said this since it had proposals at all.
        return (
            "<b>No confirmation card was created, so nothing has happened.</b>\n"
            + escape(error.strip()),
            None,
        )
    # Two actions, one card. They differ in whether a candidate id is required:
    # a reply belongs to one, a new message belongs to nothing.
    action = payload.get("action")
    if action == "send_new_email":
        required: tuple[str, ...] = ("to", "subject", "body")
    elif action == "send_email":
        required = ("candidate_email_id", "to", "subject", "body")
    else:
        return None
    if any(not payload.get(key) for key in required):
        return None
    if action == "send_email" and (
        not isinstance(payload["candidate_email_id"], int) or payload["candidate_email_id"] <= 0
    ):
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
        "<b>New email ready to send</b>" if action == "send_new_email" else "<b>Email ready to send</b>",
        f"<b>To:</b> {escape(payload['to'])}",
        f"<b>Subject:</b> {escape(payload['subject'])}",
    ]
    if action == "send_new_email":
        # A reply lands under something the reader recognises. This one does
        # not, so the card says what it is before it says anything else.
        lines.insert(1, "<i>This starts a new thread.</i>")
    cc = str(payload.get("cc") or "").strip()
    if cc:
        lines.append(f"<b>CC:</b> {escape(cc)}")
    elif payload.get("cc_changed"):
        # Said out loud, because an absent line reads as "unchanged" and this
        # one means the CC the thread had is being dropped.
        lines.append("<b>CC:</b> none")
    if payload.get("to_changed"):
        # The address no longer matches the thread, so recognising it is not
        # enough - the user has to be told it moved.
        lines.append("<i>Note: this goes to a different address than the thread.</i>")
    unknown = [str(value) for value in (payload.get("unknown_recipients") or []) if str(value).strip()]
    if unknown:
        # Named, not counted. "One unknown recipient" is not something a user
        # can check; the address is.
        lines.append(
            "<b>Not in your records:</b> " + ", ".join(escape(value) for value in unknown)
        )
    attachment_names = [*document_names]
    resume_name = str(payload.get("resume_name") or "").strip()
    if resume_name:
        attachment_names.append(resume_name)
    if attachment_names:
        lines.append("<b>Attachments:</b> " + ", ".join(escape(name) for name in attachment_names))
    lines.extend(("<b>Body preview:</b>", f"<pre>{escape(preview)}</pre>"))
    keyboard = [[
        {"text": "Send", "callback_data": f"act:prop:send:{message_id}"},
        {"text": "Cancel", "callback_data": f"act:prop:cancel:{message_id}"},
    ]]
    assert all(len(button["callback_data"].encode()) <= 64 for row in keyboard for button in row)
    return "\n".join(lines), keyboard


def proposal_card(
    tool_name: str,
    content: str,
    message_id: int,
) -> tuple[str, list[list[dict[str, str]]] | None] | None:
    if tool_name in EMAIL_PROPOSAL_TOOLS:
        return email_proposal(content, message_id)
    action = PROPOSAL_ACTIONS.get(tool_name)
    if action is None:
        return None
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
        return f"I need {escape(', '.join(missing))} before I can prepare that action.", None
    error = payload.get("error")
    if isinstance(error, str) and error.strip():
        return (
            "<b>No confirmation card was created, so nothing has happened.</b>\n"
            + escape(error.strip()),
            None,
        )
    if not payload.get("action"):
        return None
    try:
        rows = action.summary(payload)
        confirm_label = action.confirm_label(payload).strip() or "Confirm"
        reversible = action.reversible(payload) if callable(action.reversible) else action.reversible
    except (KeyError, TypeError, ValueError):
        return None
    if not rows:
        return None
    lines = ["<b>Action ready to confirm</b>"]
    lines.extend(f"<b>{escape(label)}:</b> {escape(value)}" for label, value in rows)
    lines.append(f"<b>Reversible:</b> {'Yes' if reversible else 'No'}")
    keyboard = [[
        {"text": confirm_label, "callback_data": f"act:prop:send:{message_id}"},
        {"text": "Cancel", "callback_data": f"act:prop:cancel:{message_id}"},
    ]]
    assert all(len(button["callback_data"].encode()) <= 64 for row in keyboard for button in row)
    return "\n".join(lines), keyboard


def unicode_chart(content: str) -> str | None:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("action") != "render_chart":
        return None
    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        return "Chart omitted: source provenance was missing."
    series = payload.get("series")
    max_value = payload.get("max_value")
    if (
        not isinstance(payload.get("chart_type"), str)
        or not payload["chart_type"]
        or not isinstance(series, list)
        or isinstance(max_value, bool)
        or not isinstance(max_value, (int, float))
        or max_value < 0
        or not isinstance(provenance.get("source"), str)
        or not provenance["source"]
        or isinstance(provenance.get("row_count"), bool)
        or not isinstance(provenance.get("row_count"), int)
    ):
        return None
    points: list[tuple[str, float, object]] = []
    for point in series:
        if not isinstance(point, dict) or not isinstance(point.get("label"), str):
            return None
        value = point.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        points.append((point["label"][:40], float(value), point.get("rate_of_previous")))
    title = str(payload.get("title") or "Chart")
    if not points:
        lines = [title, "No data in this range."]
    else:
        label_width = max(len(label) for label, _, _ in points)
        lines = [title]
        is_funnel = payload["chart_type"] in {"resume_funnel", "application_pipeline"}
        for label, value, rate in points:
            ratio = min(1.0, max(0.0, value / max_value)) if max_value else 0.0
            filled = int(ratio * 8 + 0.5)
            bar = "█" * filled + " " * (8 - filled)
            shown = f"{value:g}"
            if is_funnel and isinstance(rate, (int, float)) and not isinstance(rate, bool):
                shown += f" ({float(rate):g}%)"
            lines.append(f"{label:<{label_width}}  {bar}  {shown}")
    source = str(provenance["source"])
    source_line = f"Source: {source} · {provenance['row_count']} rows"
    date_range = provenance.get("date_range")
    if isinstance(date_range, dict) and date_range.get("from") and date_range.get("to"):
        source_line += f" · {date_range['from']} → {date_range['to']}"
    lines.append(source_line)
    assumptions = provenance.get("assumptions")
    if isinstance(assumptions, list):
        clean = [str(value) for value in assumptions if str(value).strip()]
        if clean:
            lines.append("Assumptions: " + " ".join(clean))
    return f"<pre>{escape(chr(10).join(lines))}</pre>"
