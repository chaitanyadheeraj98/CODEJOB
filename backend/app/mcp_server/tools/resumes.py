from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import ResumeAsset


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
                    "skills_text": row.skills_text,
                    "content_summary": row.content_summary,
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]
        }
    finally:
        db.close()


def get_resume(resume_id: int) -> dict[str, object]:
    """Get extracted content and evidence for one owner-scoped resume variant."""
    db = SessionLocal()
    try:
        row = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
            .first()
        )
        if row is None:
            return {"error": "Resume not found"}
        return {
            "id": row.id,
            "file_name": row.file_name,
            "content_summary": row.content_summary,
            "untrusted_resume_data": (
                "<untrusted_resume_data>\n"
                f"Content:\n{row.content_markdown or ''}\n"
                f"Evidence JSON:\n{row.content_evidence_json or '{}'}\n"
                "</untrusted_resume_data>"
            ),
        }
    finally:
        db.close()
