from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import ResumeAsset
from app.services import role_target_service


def list_resumes(limit: int = 10) -> dict[str, object]:
    """List the owner's uploaded resumes: name, version, enabled/current state."""
    db = SessionLocal()
    try:
        rows = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == settings.owner_id)
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


def get_resume(resume_id: int = 0, variant: str = "") -> dict[str, object]:
    """Get the full text of one resume, by id or by variant name."""
    db = SessionLocal()
    try:
        matched_field = ""
        if resume_id > 0:
            row = (
                db.query(ResumeAsset)
                .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
                .first()
            )
        elif variant.strip():
            query = variant.strip()
            rows = []
            for field in ("variant_label", "primary_role", "file_name"):
                column = getattr(ResumeAsset, field)
                rows = (
                    db.query(ResumeAsset)
                    .filter(ResumeAsset.owner_id == settings.owner_id, column.ilike(f"%{query}%"))
                    .all()
                )
                if rows:
                    matched_field = field
                    break
            if not rows:
                all_rows = db.query(ResumeAsset).filter(ResumeAsset.owner_id == settings.owner_id).all()
                return {
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
                return {
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
                .filter(ResumeAsset.owner_id == settings.owner_id)
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
        else:
            return {"status": "missing_fields", "missing": ["resume_id or variant"]}
        if row is None:
            return {"error": "Resume not found"}
        if row.content_markdown is None:
            return {
                "id": row.id,
                "file_name": row.file_name,
                "error": "No text could be extracted from this resume file.",
            }
        other_versions = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == settings.owner_id)
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
                "<untrusted_resume_data>\n"
                f"Content:\n{row.content_markdown}\n"
                f"Evidence JSON:\n{row.content_evidence_json or '{}'}\n"
                "</untrusted_resume_data>"
            ),
        }
    finally:
        db.close()


def analyse_role_target(role: str = "", window_days: int = 365) -> dict[str, object]:
    """What it would take to apply for a named role: closest resume, and the missing keywords.

    Use for "can I apply for X?", "what am I missing for X?", "what keywords should I add
    for X?" - including roles the app has no family for, like security or QA.
    """
    if not role.strip():
        return {"status": "missing_fields", "missing": ["role"]}
    db = SessionLocal()
    try:
        report = role_target_service.analyse_role_target(
            db,
            owner_id=settings.owner_id,
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
