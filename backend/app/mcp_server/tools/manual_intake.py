from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import RecentRun
from app.services.chat_provenance import user_supplied_document
from app.services.manual_intake_service import manual_intake_length_error, preview_duplicate
from app import tenancy


def propose_manual_requirement(attachment_id: int = 0, message_id: int = 0) -> dict[str, object]:
    """Prepare a pasted or attached job requirement for review. Never ingests it.

    Call this when the user gives you a job description and wants it tracked.
    Pass attachment_id for a file they uploaded, or message_id for a JD pasted
    into a message. Never pass text: it is read from the database.

    Only the user's click on the confirmation card creates anything.
    """
    db = SessionLocal()
    try:
        document = user_supplied_document(
            db,
            tenancy.owner_id(),
            attachment_id=attachment_id or None,
            message_id=message_id or None,
        )
        if document is None:
            return {
                "status": "no_document",
                "detail": "No readable pasted or attached job requirement was found.",
            }
        if not document.text.strip():
            return {
                "status": "no_document",
                "detail": "No readable pasted or attached job requirement was found.",
            }
        error = manual_intake_length_error(document.text)
        if error:
            return {
                "status": "too_long",
                "characters": len(document.text),
                "limit": settings.manual_intake_max_chars,
                "detail": error,
            }
        duplicate = preview_duplicate(db, owner_id=tenancy.owner_id(), text=document.text)
        return {
            "action": "propose_manual_requirement",
            "source": document.origin,
            "source_label": document.label,
            "attachment_id": document.attachment_id,
            "message_id": document.message_id,
            "characters": len(document.text),
            "jd_text": document.text,
            "duplicate_of": (
                {
                    "id": duplicate.id,
                    "role": duplicate.role,
                    "client": duplicate.client,
                    "created_at": duplicate.created_at.isoformat(),
                }
                if duplicate
                else None
            ),
        }
    finally:
        db.close()


def check_manual_intake(run_key: str = "") -> dict[str, object]:
    """Report on a requirement the user confirmed - running, or what it became.

    Call this once after a confirmed intake card, and whenever the user asks
    whether it finished. Omit run_key for the most recent one.
    """
    db = SessionLocal()
    try:
        query = (
            db.query(RecentRun)
            .filter(
                RecentRun.owner_id == tenancy.owner_id(),
                RecentRun.run_key.like("manual_intake:%"),
            )
            .order_by(RecentRun.created_at.desc())
        )
        row = query.filter(RecentRun.run_key == run_key).first() if run_key else query.first()
        if row is None:
            return {
                "found": False,
                "instruction": (
                    f"No manual intake matching '{run_key}' has been started; "
                    "this is not an intake that found nothing."
                    if run_key
                    else "No manual intake has been started; this is not an intake that found nothing."
                ),
            }
        status = row.status or ""
        return {
            "found": True,
            "run_key": row.run_key,
            "status": status,
            "finished": status in {"ok", "failed"},
            "detail": row.detail or "",
            "started_at": row.created_at.isoformat() if row.created_at else None,
        }
    finally:
        db.close()
