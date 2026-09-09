"""Read a resume draft, and propose rewriting one section of it.

The assistant writes the resume here. It does so a section at a time, and for
two reasons that both matter.

A whole resume is thousands of tokens emitted inside a single tool call, which
does not survive the chat timeout - and a rewrite that times out truncates, which
looks like a finished resume that stops mid-bullet. A section fits.

A whole resume is also unreadable on a confirmation card. The point of the card
is that the user sees what changes before it happens, and "here is your entire
resume again, spot the difference" is not that. One section shows as its old text
beside its new text, which is a thing a person can actually check.
"""

from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import ResumeDraft
from app.services.resume_grounding_service import check_grounding
from app.services.resume_render_service import (
    covers_whole_document,
    find_section,
    nested_headings,
    section_digest,
    split_sections,
)

# A ceiling on one rewrite, well under the 50,000 the draft itself allows. It is
# not a storage limit - it is the size above which the model is being asked to
# write more than one section at a time, which is the thing this tool exists to
# stop.
MAX_SECTION_CHARS = 8000


def _draft(db, draft_id: int) -> ResumeDraft | None:
    return (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == settings.owner_id, ResumeDraft.id == int(draft_id))
        .first()
    )


def _resolve_draft(db, draft_id: int = 0, name: str = ""):
    """Find the one draft meant, by id or by name. Returns (draft, refusal).

    Naming a draft matters more than it looks: a turn has a budget of a few tool
    calls, and making the model list every draft just to turn "the R15 one" into
    an id spends one of them on bookkeeping. Resolving a name here is what keeps
    read-then-rewrite inside two calls.
    """
    if draft_id > 0:
        draft = _draft(db, draft_id)
        return (draft, None) if draft is not None else (None, _not_found(db, draft_id))

    query = name.strip()
    if not query:
        return None, {"status": "missing_fields", "missing": ["draft_id or name"]}

    rows = (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == settings.owner_id, ResumeDraft.name.ilike(f"%{query}%"))
        .order_by(ResumeDraft.updated_at.desc())
        .all()
    )
    if not rows:
        return None, {
            "status": "not_found",
            "name": name,
            "drafts": _all_drafts(db),
        }
    if len(rows) > 1:
        # Two drafts copied from the same variant is a normal thing to have, and
        # guessing which one to rewrite is how an hour of edits disappears.
        return None, {
            "status": "ambiguous",
            "name": name,
            "matches": [
                {"id": row.id, "name": row.name, "characters": len(row.content_markdown or ""),
                 "updated_at": row.updated_at.isoformat()}
                for row in rows
            ],
            "instruction": "Ask which one. Do not pick.",
        }
    return rows[0], None


def _all_drafts(db) -> list[dict[str, object]]:
    rows = (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == settings.owner_id)
        .order_by(ResumeDraft.updated_at.desc())
        .all()
    )
    return [{"id": row.id, "name": row.name} for row in rows]


def _not_found(db, draft_id: int) -> dict[str, object]:
    """Say which drafts do exist, so a wrong id is one question, not two."""
    rows = (
        db.query(ResumeDraft)
        .filter(ResumeDraft.owner_id == settings.owner_id)
        .order_by(ResumeDraft.updated_at.desc())
        .all()
    )
    return {
        "error": "Draft not found",
        "draft_id": int(draft_id),
        "drafts": [{"id": row.id, "name": row.name} for row in rows],
    }


def list_resume_drafts() -> dict[str, object]:
    """List the resume drafts in the Editor, with the sections each one has.

    For "what drafts do I have" and for offering a choice. A rewrite does not
    need this - `get_resume_draft` takes a draft name directly, and spending a
    call here first is what pushes a rewrite past the tool budget for a turn.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(ResumeDraft)
            .filter(ResumeDraft.owner_id == settings.owner_id)
            .order_by(ResumeDraft.updated_at.desc(), ResumeDraft.id.desc())
            .all()
        )
        return {
            "count": len(rows),
            "drafts": [
                {
                    "id": row.id,
                    "name": row.name,
                    "source_resume_id": row.source_resume_id,
                    "characters": len(row.content_markdown or ""),
                    "sections": [
                        {"heading": item.heading, "characters": len(item.body)}
                        for item in split_sections(row.content_markdown or "")
                    ],
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ],
        }
    finally:
        db.close()


def get_resume_draft(draft_id: int = 0, name: str = "", section: str = "") -> dict[str, object]:
    """Read a draft's text, or one named section of it. Name it by id or by name.

    This is the first of the two calls a rewrite takes: ask for the section you
    are about to change, by draft name if you do not have an id, and the reply
    carries the draft_id `propose_resume_section` needs.

    Read the draft, not the source variant. A rewrite composed from the variant
    quietly undoes whatever the user has already changed in the Editor.
    """
    db = SessionLocal()
    try:
        draft, refusal = _resolve_draft(db, draft_id, name)
        if refusal is not None:
            return refusal
        assert draft is not None
        content = draft.content_markdown or ""
        headings = [item.heading for item in split_sections(content)]

        if section.strip():
            found = find_section(content, section)
            if found is None:
                return {
                    "status": "section_not_found",
                    "draft_id": draft.id,
                    "section": section,
                    "sections": headings,
                    "instruction": "Use one of these names exactly. Do not invent a section.",
                }
            return {
                "draft_id": draft.id,
                "draft_name": draft.name,
                "section": found.heading,
                "characters": len(found.body),
                "untrusted_resume_data": (
                    f"<untrusted_resume_data>\n{found.body}\n</untrusted_resume_data>"
                ),
            }

        return {
            "draft_id": draft.id,
            "draft_name": draft.name,
            "characters": len(content),
            "sections": headings,
            "untrusted_resume_data": (
                f"<untrusted_resume_data>\n{content}\n</untrusted_resume_data>"
            ),
        }
    finally:
        db.close()


def propose_resume_section(
    draft_id: int = 0, section: str = "", replacement: str = "", name: str = "",
    sequence_sections: list[str] | None = None,
) -> dict[str, object]:
    """Prepare a rewrite of one section of a draft. Never performs it.

    This is how you write the resume. Call `get_resume_draft` for the section
    first, write the new version of that section yourself, and pass it here as
    `replacement` - the complete new body of that one section, without its `###`
    heading line.

    Rewrite one section per call. Tailoring a resume to a job is several of these
    in sequence - Summary, then Skills, then a role's bullets - not one call
    carrying the whole document.

    The card shows the current text beside yours, and only the user's click
    applies it. Everything outside the named section is left exactly as it is.
    """
    text = (replacement or "").replace("\r\n", "\n").strip()
    if not section.strip() or not text:
        return {
            "status": "missing_fields",
            "missing": [name for name, value in (("section", section.strip()), ("replacement", text)) if not value],
        }
    if len(text) > MAX_SECTION_CHARS:
        return {
            "status": "too_long",
            "characters": len(text),
            "limit": MAX_SECTION_CHARS,
            "instruction": (
                "That is more than one section. Rewrite a single section per call - "
                "Summary, then Skills, then one role at a time."
            ),
        }

    db = SessionLocal()
    try:
        draft, refusal = _resolve_draft(db, draft_id, name)
        if refusal is not None:
            return refusal
        assert draft is not None
        content = draft.content_markdown or ""
        found = find_section(content, section)
        if found is None:
            return {
                "status": "section_not_found",
                "draft_id": draft.id,
                "section": section,
                "sections": [item.heading for item in split_sections(content)],
                "instruction": "Use one of these names exactly. Do not invent a section.",
            }
        if covers_whole_document(content, found):
            inside = nested_headings(content, found)
            return {
                "status": "section_too_broad",
                "draft_id": draft.id,
                "section": found.heading,
                "contains_sections": inside,
                "instruction": (
                    f"“{found.heading}” contains the whole resume - replacing it would delete "
                    f"{', '.join(inside)}. Rewrite one of those instead, one per card. "
                    "There is no tool that replaces a resume wholesale, and this is not a way "
                    "to get one: if the user wants that, they do it in the Editor themselves."
                ),
            }
        return {
            "action": "propose_resume_section",
            "draft_id": draft.id,
            "draft_name": draft.name,
            "section": found.heading,
            # Read from the database, never from the model: only the server can
            # supply the "before" half of before -> after honestly.
            "current": found.body,
            "current_characters": len(found.body),
            "replacement": text,
            "replacement_characters": len(text),
            "grounding": check_grounding(content, found.body, text),
            "sequence_sections": list(dict.fromkeys(
                item.heading for heading in (sequence_sections or [])[:20]
                if (item := find_section(content, heading)) is not None
                and not covers_whole_document(content, item)
            )),
            # Stamped now, checked at the click. A rewrite composed against text
            # the user has since edited is refused rather than overwriting it.
            "base_sha256": section_digest(found.body),
        }
    finally:
        db.close()
