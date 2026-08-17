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
                    "updated_at": row.updated_at.isoformat(),
                }
                for row in rows
            ]
        }
    finally:
        db.close()
