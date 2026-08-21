from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import RecruiterEmail


def propose_send_email(candidate_email_id: int, body: str, subject_override: str = "") -> dict[str, object]:
    """Prepare a body-only reply proposal for an existing owner-scoped email thread."""
    db = SessionLocal()
    try:
        row = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == settings.owner_id,
                RecruiterEmail.id == candidate_email_id,
            )
            .first()
        )
        if row is None:
            return {"error": "Candidate not found"}
        if not (row.recipient_email or "").strip():
            return {
                "status": "missing_fields",
                "missing": ["recipient_email"],
                "note": "No recipient on file for this email - resolve it in the app first.",
            }
        if not body.strip():
            return {"status": "missing_fields", "missing": ["body"]}
        return {
            "action": "send_email",
            "candidate_email_id": row.id,
            "to": row.recipient_email,
            "cc": row.cc_email,
            "subject": subject_override.strip() or f"Re: {row.subject}",
            "body": body.strip(),
        }
    finally:
        db.close()
