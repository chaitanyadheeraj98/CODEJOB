from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import CandidateDocument, RecruiterEmail

# Matches the cap on ChatSendReplyRequest.document_ids, so a proposal that the
# server would refuse is never drawn as a confirmable card.
MAX_DOCUMENT_IDS = 20


def propose_send_email(
    candidate_email_id: int,
    body: str,
    subject_override: str = "",
    document_ids: list[int] | None = None,
) -> dict[str, object]:
    """Prepare a body-only reply proposal for an existing owner-scoped email thread.

    Pass `document_ids` to attach the user's stored documents - get the ids from
    list_candidate_documents, and never invent one. The confirmation card names
    every file it will send, so attaching the wrong document is something the
    user can see before it goes.
    """
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
            return {"hint": 'Ask the user for recipient_email. Do not guess.', 
                "status": "missing_fields",
                "missing": ["recipient_email"],
                "note": "No recipient on file for this email - resolve it in the app first.",
            }
        if not body.strip():
            return {"hint": 'Ask the user for body. Do not guess.', "status": "missing_fields", "missing": ["body"]}

        requested = list(dict.fromkeys(int(value) for value in (document_ids or [])))[
            :MAX_DOCUMENT_IDS
        ]
        documents: list[CandidateDocument] = []
        if requested:
            found = {
                item.id: item
                for item in db.query(CandidateDocument).filter(
                    CandidateDocument.owner_id == settings.owner_id,
                    CandidateDocument.id.in_(requested),
                )
            }
            # Reported rather than dropped: an id the user asked for and did not
            # get is the one thing they cannot see on a card that lists what
            # *will* be sent.
            unknown = [value for value in requested if value not in found]
            if unknown:
                return {
                    "error": f"Unknown document ids: {', '.join(str(value) for value in unknown)}",
                    "note": "Call list_candidate_documents and use the ids it returns.",
                }
            documents = [found[value] for value in requested]

        return {
            "action": "send_email",
            "candidate_email_id": row.id,
            "to": row.recipient_email,
            "cc": row.cc_email,
            "subject": subject_override.strip() or f"Re: {row.subject}",
            "body": body.strip(),
            "document_ids": [item.id for item in documents],
            # Names, for the card. The client sends the ids and the server
            # re-resolves them, so this list is display only.
            "document_names": [item.file_name for item in documents],
        }
    finally:
        db.close()
