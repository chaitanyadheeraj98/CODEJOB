from __future__ import annotations

from app.mcp_server.tools import untrusted
from app.config import settings
from app.db import SessionLocal
from app.models import RecentRun, RecentRunSkippedItem
from app.recent_runs import row_to_recent_run_dict


def _json_ready(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: value.isoformat() if hasattr(value, "isoformat") else value
        for key, value in payload.items()
    }


def get_recent_runs(limit: int = 10) -> dict[str, object]:
    """List the owner's most recent automation and sync runs."""
    db = SessionLocal()
    try:
        rows = (
            db.query(RecentRun)
            .filter(RecentRun.owner_id == settings.owner_id)
            .order_by(RecentRun.created_at.desc(), RecentRun.id.desc())
            .limit(max(1, min(limit, 25)))
            .all()
        )
        return {"runs": [_json_ready(row_to_recent_run_dict(row)) for row in rows]}
    finally:
        db.close()


def get_run_items(run_key: str, limit: int = 25) -> dict[str, object]:
    """List read-only item outcomes for one owner-scoped recent run."""
    db = SessionLocal()
    try:
        rows = (
            db.query(RecentRunSkippedItem)
            .filter(
                RecentRunSkippedItem.owner_id == settings.owner_id,
                RecentRunSkippedItem.run_key == run_key,
            )
            .order_by(RecentRunSkippedItem.created_at.desc(), RecentRunSkippedItem.id.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        return {
            "run_key": run_key,
            "items": [
                {
                    "id": row.id,
                    "outcome": row.outcome,
                    "reason_code": row.reason_code,
                    "candidate_email_id": row.candidate_email_id,
                    "created_at": row.created_at.isoformat(),
                    "untrusted_run_item_data": (
                        untrusted("run_item",
                        f"Title: {row.title_or_subject}\nReason: {row.reason_detail}")
                    ),
                }
                for row in rows
            ],
        }
    finally:
        db.close()
