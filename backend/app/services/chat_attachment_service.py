from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ChatAttachment
from app.parsing.document_extraction import extract_document_text

# Extension -> (accepted mime types, magic-byte prefixes). An empty prefix tuple
# means the format has no signature and is validated by decoding instead.
ALLOWED_TYPES: dict[str, tuple[tuple[str, ...], tuple[bytes, ...]]] = {
    ".pdf": (("application/pdf",), (b"%PDF-",)),
    ".docx": (
        (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        ),
        (b"PK\x03\x04",),
    ),
    ".txt": (("text/plain",), ()),
    ".csv": (("text/csv", "text/plain", "application/csv", "application/vnd.ms-excel"), ()),
}

_CHUNK = 64 * 1024


def _extension(file_name: str) -> str:
    return Path(file_name).suffix.lower()


def _safe_display_name(file_name: str) -> str:
    """Keep a readable name for the UI. It never touches the filesystem."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", "", Path(file_name).name).strip()
    return (cleaned or "attachment")[:255]


def _looks_like_text(payload: bytes) -> bool:
    if b"\x00" in payload:
        return False
    for encoding in ("utf-8", "cp1252"):
        try:
            payload.decode(encoding)
            return True
        except UnicodeDecodeError:
            continue
    return False


class ChatAttachmentService:
    @staticmethod
    def storage_dir() -> Path:
        return Path(settings.chat_attachment_storage_dir)

    @staticmethod
    def read_capped(stream: BinaryIO) -> bytes:
        """Read the body, refusing anything over the cap *while* reading it.

        Buffering first and checking the length afterwards means a 2GB upload is
        already in memory by the time it is rejected.
        """
        cap = settings.chat_attachment_max_bytes
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise HTTPException(
                    status_code=413,
                    detail=f"Attachments are limited to {cap // (1024 * 1024)} MB",
                )
            chunks.append(chunk)
        if not total:
            raise HTTPException(status_code=400, detail="Empty file not allowed")
        return b"".join(chunks)

    @staticmethod
    def validate_type(file_name: str, content_type: str | None, payload: bytes) -> str:
        extension = _extension(file_name)
        if extension not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type '{extension or file_name}'. Allowed: "
                + ", ".join(sorted(ALLOWED_TYPES)),
            )
        mime_types, signatures = ALLOWED_TYPES[extension]
        if signatures:
            if not any(payload.startswith(signature) for signature in signatures):
                # A .pdf whose bytes are a zip is either a mistake or an attack.
                # Both deserve an error rather than a silent correction.
                raise HTTPException(
                    status_code=415,
                    detail=f"File contents do not match its {extension} extension",
                )
        elif not _looks_like_text(payload):
            raise HTTPException(
                status_code=415,
                detail=f"File contents do not match its {extension} extension",
            )
        declared = (content_type or "").split(";")[0].strip().lower()
        return declared if declared in mime_types else mime_types[0]

    @classmethod
    def extract(cls, path: Path, file_name: str) -> tuple[str | None, str | None]:
        """Returns (markdown, error). A failure never fails the upload: a file
        that cannot be parsed is still a file the user attached, and losing it
        silently is worse than holding an unreadable one."""
        max_chars = settings.chat_attachment_max_extract_chars
        try:
            # Plain text goes straight through. partition() renders a CSV worse
            # than its own source does, and for .txt it is actively destructive:
            # the fallback path has extractors for pdf and docx only, so a
            # failed partition throws the file's own readable text away and
            # returns "extraction is limited" instead.
            if _extension(file_name) in {".csv", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                return text[:max_chars], None
            return extract_document_text(
                str(path), file_name, max_chars=max_chars, label="Document"
            ).markdown_text, None
        except Exception as exc:  # noqa: BLE001 - surfaced to the user, not swallowed
            return None, str(exc)[:2000]

    @classmethod
    def create(
        cls, db: Session, session_id: int, file_name: str, content_type: str | None, stream: BinaryIO
    ) -> ChatAttachment:
        payload = cls.read_capped(stream)
        display_name = _safe_display_name(file_name)
        mime_type = cls.validate_type(display_name, content_type, payload)
        sha256 = hashlib.sha256(payload).hexdigest()

        existing = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == settings.owner_id,
                ChatAttachment.sha256 == sha256,
                ChatAttachment.session_id == session_id,
            )
            .first()
        )
        if existing is not None:
            return existing

        directory = cls.storage_dir()
        directory.mkdir(parents=True, exist_ok=True)
        # Derived from the hash, never from the supplied name: a filename is
        # user input and has no business deciding a path.
        target = directory / f"{sha256}{_extension(display_name)}"
        if not target.exists():
            target.write_bytes(payload)

        markdown, error = cls.extract(target, display_name)
        row = ChatAttachment(
            owner_id=settings.owner_id,
            session_id=session_id,
            file_path=str(target),
            file_name=display_name,
            mime_type=mime_type,
            byte_size=len(payload),
            sha256=sha256,
            content_markdown=markdown,
            extraction_error=error,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    @staticmethod
    def list_for_session(db: Session, session_id: int) -> list[ChatAttachment]:
        return (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == settings.owner_id,
                ChatAttachment.session_id == session_id,
            )
            .order_by(ChatAttachment.id.asc())
            .all()
        )

    @staticmethod
    def get(db: Session, attachment_id: int) -> ChatAttachment:
        row = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == settings.owner_id,
                ChatAttachment.id == attachment_id,
            )
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return row

    @classmethod
    def bind_to_message(
        cls, db: Session, session_id: int, message_id: int, attachment_ids: list[int]
    ) -> list[ChatAttachment]:
        if not attachment_ids:
            return []
        rows = (
            db.query(ChatAttachment)
            .filter(
                ChatAttachment.owner_id == settings.owner_id,
                ChatAttachment.session_id == session_id,
                ChatAttachment.id.in_(attachment_ids),
                ChatAttachment.message_id.is_(None),
            )
            .all()
        )
        for row in rows:
            row.message_id = message_id
        return rows

    @staticmethod
    def note_for(rows: list[ChatAttachment]) -> str:
        """The only way the model learns these ids.

        MCP tools are stateless and carry no chat session, so nothing else in
        the turn tells it what was attached.
        """
        if not rows:
            return ""
        listed = ", ".join(f'{row.id} "{row.file_name}"' for row in rows)
        return (
            f"\n\n[Attached: {len(rows)} file{'s' if len(rows) != 1 else ''} — {listed}. "
            "Call read_chat_attachment with an id to read one.]"
        )

    @classmethod
    def delete(cls, db: Session, attachment_id: int) -> None:
        row = cls.get(db, attachment_id)
        path = Path(row.file_path)
        sha256 = row.sha256
        db.delete(row)
        db.commit()
        # The stored path is the hash, so two rows can point at one file.
        still_referenced = (
            db.query(ChatAttachment).filter(ChatAttachment.sha256 == sha256).count() > 0
        )
        if not still_referenced and path.exists():
            path.unlink(missing_ok=True)
