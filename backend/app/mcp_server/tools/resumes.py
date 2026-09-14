from __future__ import annotations

from app.mcp_server.tools import untrusted
from app.config import settings
from app.db import SessionLocal
from app.models import ResumeAsset
from app.schemas import resume_variant_code
from app.services import role_target_service
from app.services.resume_render_service import find_section, split_sections
from app import tenancy


def list_resumes(limit: int = 10) -> dict[str, object]:
    """List the owner's uploaded resumes: name, version, enabled/current state."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == tenancy.owner_id())
            .order_by(ResumeAsset.is_current.desc(), ResumeAsset.updated_at.desc())
            .limit(max(1, min(limit, 25)))
            .all()
        )
        return {
            "resumes": [
                {
                    "id": row.id,
                    "file_name": row.file_name,
                    "version": row.version,
                    "is_current": row.is_current,
                    "is_enabled": row.is_enabled,
                    "variant_label": row.variant_label,
                    "primary_role": row.primary_role,
                    "readable": row.content_markdown is not None,
                    "skills_text": row.skills_text,
                    "content_summary": row.content_summary,
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]
        }
    finally:
        db.close()


def _resolve_variant(
    db, resume_id: int = 0, variant: str = ""
) -> tuple[ResumeAsset | None, dict[str, object] | None]:
    """Find the one resume the user meant, by id or by name.

    Shared by every tool that has to turn "my Java resume" into a row, so naming
    a resume badly fails the same way whether the user is reading one or drafting
    from it. Returns the row or the envelope to hand back - never both, and never
    a guess: an ambiguous name comes back as a question, not a pick.
    """
    if resume_id > 0:
        row = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == tenancy.owner_id(), ResumeAsset.id == resume_id)
            .first()
        )
        return (row, None) if row is not None else (None, {"error": "Resume not found"})

    if not variant.strip():
        return None, {"hint": 'Ask the user for resume_id or variant. Do not guess.', "status": "missing_fields", "missing": ["resume_id or variant"]}

    query = variant.strip()
    matched_field = ""
    rows: list[ResumeAsset] = []
    for field in ("variant_label", "primary_role", "file_name"):
        column = getattr(ResumeAsset, field)
        rows = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == tenancy.owner_id(), column.ilike(f"%{query}%"))
            .all()
        )
        if rows:
            matched_field = field
            break
    if not rows:
        all_rows = db.query(ResumeAsset).filter(ResumeAsset.owner_id == tenancy.owner_id()).all()
        return None, {
            "status": "not_found",
            "variant": variant,
            "known": sorted(
                {
                    name
                    for item in all_rows
                    for name in (item.variant_label, item.primary_role, item.file_name)
                    if name
                },
                key=str.casefold,
            ),
        }
    by_name: dict[str, list[ResumeAsset]] = {}
    for item in rows:
        by_name.setdefault(str(getattr(item, matched_field) or "").casefold(), []).append(item)
    if len(by_name) > 1:
        matches = [max(items, key=lambda item: (item.version, item.id)) for items in by_name.values()]
        return None, {
            "status": "ambiguous",
            "matches": [
                {
                    "id": item.id,
                    "file_name": item.file_name,
                    "variant_label": item.variant_label,
                    "primary_role": item.primary_role,
                    "version": item.version,
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in sorted(matches, key=lambda item: str(getattr(item, matched_field)).casefold())
            ],
            "instruction": "Ask which one. Do not pick.",
        }
    row = max(rows, key=lambda item: (item.version, item.id))
    same_file_rows = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == tenancy.owner_id())
        .all()
    )
    row = max(
        (
            item
            for item in same_file_rows
            if item.file_name.casefold() == row.file_name.casefold()
        ),
        key=lambda item: (item.version, item.id),
    )
    return row, None


def get_resume(resume_id: int = 0, variant: str = "", section: str = "") -> dict[str, object]:
    """Read one resume by id or variant name, optionally returning only a named section."""
    db = SessionLocal()
    try:
        row, refusal = _resolve_variant(db, resume_id, variant)
        if refusal is not None:
            return refusal
        assert row is not None
        if row.content_markdown is None:
            return {
                "id": row.id,
                "file_name": row.file_name,
                "error": "No text could be extracted from this resume file.",
            }
        if section.strip():
            headings = [item.heading for item in split_sections(row.content_markdown)]
            found = find_section(row.content_markdown, section)
            if found is None:
                return {"status": "section_not_found", "status_code": 404, "id": row.id,
                        "sections": headings, "hint": "Use one of the returned headings exactly."}
            return {"id": row.id, "file_name": row.file_name, "section": found.heading,
                    "sections": headings, "characters": len(found.body),
                    "untrusted_resume_data": untrusted("resume", found.body)}
        other_versions = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == tenancy.owner_id())
            .all()
        )
        return {
            "id": row.id,
            "file_name": row.file_name,
            "content_summary": row.content_summary,
            "version": row.version,
            "is_current": row.is_current,
            "variant_label": row.variant_label,
            "updated_at": row.updated_at.isoformat(),
            "other_versions": sum(
                1
                for item in other_versions
                if item.file_name.casefold() == row.file_name.casefold() and item.version < row.version
            ),
            "untrusted_resume_data": (
                untrusted("resume",
                f"Content:\n{row.content_markdown}\n"
                f"Evidence JSON:\n{row.content_evidence_json or '{}'}")
            ),
        }
    finally:
        db.close()


def propose_resume_draft(source_resume_id: int = 0, variant: str = "", name: str = "",
                         from_scratch: bool = False, candidate_name: str = "", contact_line: str = "") -> dict[str, object]:
    """Prepare a resume draft copied from one of the user's variants. Never creates it.

    Call this when the user asks you to draft, tailor, or rewrite a resume. Say
    which variant to start from by id, or by name for "my Java resume" - the
    same names get_resume accepts.

    Do not pass the resume text. The server copies it from the variant, so the
    draft opens in the user's own wording rather than your recollection of it,
    and the variant itself is only ever read. Only the user's click on the card
    creates the draft; until then nothing exists.
    """
    if from_scratch:
        if source_resume_id or variant.strip():
            return {"status": "invalid_source", "detail": "Choose an existing variant or start from scratch."}
        if not candidate_name.strip() or not contact_line.strip():
            return {"hint": 'Ask the user for candidate_name, contact_line. Do not guess.', "status": "missing_fields", "missing": ["candidate_name", "contact_line"]}
        if len(candidate_name) > 100 or len(contact_line) > 300 or any(c in candidate_name + contact_line for c in "\r\n"):
            return {"status": "invalid_header", "detail": "Supply only a short name and one contact line."}
        content = f"# {candidate_name.strip()}\n{contact_line.strip()}\n\n## Summary\n\n## Skills\n\n## Experience\n\n## Education"
        return {"action": "propose_resume_draft", "from_scratch": True,
                "name": (name.strip() or f"{candidate_name.strip()} draft")[:200],
                "initial_content": content, "source_characters": len(content)}
    db = SessionLocal()
    try:
        row, refusal = _resolve_variant(db, source_resume_id, variant)
        if refusal is not None:
            return refusal
        assert row is not None
        if row.content_markdown is None:
            # Seeding from a file nothing could be read out of makes an empty
            # draft, which looks like the tool worked. Say why it did not.
            return {
                "id": row.id,
                "file_name": row.file_name,
                "error": (
                    "No text could be extracted from this resume file, so a draft "
                    "copied from it would be empty."
                ),
            }
        code = resume_variant_code(row.id)
        return {
            "action": "propose_resume_draft",
            "source_resume_id": row.id,
            "source_variant_code": code,
            "source_file_name": row.file_name,
            "source_variant_label": row.variant_label or "",
            # The size of what will be copied, not the text itself: the card is a
            # confirmation that the right resume was picked, and the draft's own
            # editor is where the words get read.
            "source_characters": len(row.content_markdown),
            # Named the way create_draft would name it for a blank field, so the
            # card shows the name that will actually be stored.
            "name": (name.strip() or (f"{code} {row.variant_label}" if row.variant_label else f"{code} copy"))[:200],
        }
    finally:
        db.close()


def analyse_role_target(role: str = "", window_days: int = 365) -> dict[str, object]:
    """What it would take to apply for a named role: closest resume, and the missing keywords.

    Use for "can I apply for X?", "what am I missing for X?", "what keywords should I add
    for X?" - including roles the app has no family for, like security or QA.
    """
    if not role.strip():
        return {"hint": 'Ask the user for role. Do not guess.', "status": "missing_fields", "missing": ["role"]}
    db = SessionLocal()
    try:
        report = role_target_service.analyse_role_target(
            db,
            owner_id=tenancy.owner_id(),
            target_role=role,
            window_days=max(1, min(window_days, 730)),
        )
    finally:
        db.close()
    closest = report["variants"][0] if report["variants"] else None
    return {
        "target_role": report["target_role"],
        "window_days": report["window_days"],
        # How many job descriptions this answer is built on, and whether that is enough
        # to call it a pattern. Say so rather than presenting a thin cohort as a finding.
        "matching_jds": report["cohort_size"],
        "evidence_tier": report["evidence_tier"],
        "verdict": report["verdict"],
        "closest_variant": (
            {
                "variant_code": closest["variant_code"],
                "variant_label": closest["variant_label"],
                "coverage": closest["coverage"],
                "already_has": closest["matched_skills"],
                "missing": closest["missing_skills"],
            }
            if closest
            else None
        ),
        "add_to_resume": [
            {"skill": item["skill"], "demanded_by_jds": item["jd_count"]}
            for item in report["demanded_skills"]
            if not item["covered_by_closest"]
        ],
        "example_jds": report["sample_jds"],
    }
