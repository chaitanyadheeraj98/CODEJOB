from __future__ import annotations

from app.mcp_server.tools import untrusted
from app.config import settings
from app.db import SessionLocal
from app.models import ChatAttachment
from app import tenancy


def list_chat_attachments(session_id: int) -> dict[str, object]:
    """List the files attached to one chat session, with their ids and names.

    Use it when the user refers to "the file I attached" without giving an id.
    """
    db = SessionLocal()
    try:
        rows = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == tenancy.owner_id(),
                ChatAttachment.session_id == session_id,
            )
            .order_by(ChatAttachment.id.asc())
            .all()
        )
        return {
            "count": len(rows),
            "attachments": [
                {
                    "id": row.id,
                    "file_name": row.file_name,
                    "mime_type": row.mime_type,
                    "byte_size": row.byte_size,
                    "readable": row.content_markdown is not None,
                    "extraction_error": row.extraction_error,
                }
                for row in rows
            ],
        }
    finally:
        db.close()


def read_chat_attachment(attachment_id: int) -> dict[str, object]:
    """Read the extracted text of one attached file.

    The id appears in the user's own message when they attach something. The
    text was extracted once at upload, so calling this repeatedly is cheap.
    """
    db = SessionLocal()
    try:
        row = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == tenancy.owner_id(),
                ChatAttachment.id == attachment_id,
            )
            .first()
        )
        if row is None:
            return {"error": "Attachment not found"}
        if row.content_markdown is None:
            return {
                "id": row.id,
                "file_name": row.file_name,
                "error": row.extraction_error or "No text could be extracted from this file.",
            }
        return {
            "id": row.id,
            "file_name": row.file_name,
            "mime_type": row.mime_type,
            # An uploaded job description is exactly the attacker-controlled text
            # these delimiters exist for - the user did not write it either.
            "untrusted_document_data": (
                untrusted("document",
                f"{row.content_markdown}")
            ),
        }
    finally:
        db.close()
