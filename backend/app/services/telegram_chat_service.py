from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import tenancy
from app.config import settings
from app.models import ChatMessage, ChatSession, ChatTurn, TelegramLink


def _link(db: Session, owner_id: str, chat_id: int) -> TelegramLink:
    row = (
        db.query(TelegramLink)
        .filter(TelegramLink.owner_id == owner_id, TelegramLink.chat_id == chat_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Telegram chat is not linked")
    return row


def current_session(db: Session, owner_id: str, chat_id: int) -> ChatSession:
    link = _link(db, owner_id, chat_id)
    if link.chat_session_id is not None:
        session = (
            db.query(ChatSession)
            .filter(
                ChatSession.id == link.chat_session_id,
                ChatSession.owner_id == owner_id,
                ChatSession.origin == "telegram",
            )
            .first()
        )
        if session is not None:
            return session
    session = ChatSession(owner_id=owner_id, title="Telegram", origin="telegram")
    db.add(session)
    db.flush()
    link.chat_session_id = session.id
    db.flush()
    return session


def reset_session(db: Session, owner_id: str, chat_id: int) -> None:
    _link(db, owner_id, chat_id).chat_session_id = None
    db.flush()


def purge_expired(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(UTC)
    session_ids = [
        row[0]
        for row in db.query(ChatSession.id)
        .filter(
            ChatSession.owner_id == tenancy.owner_id(),
            ChatSession.origin == "telegram",
            ChatSession.updated_at < now - timedelta(hours=24),
        )
        .all()
    ]
    if not session_ids:
        return {"messages": 0, "sessions": 0}

    message_ids = [
        row[0]
        for row in db.query(ChatMessage.id)
        .filter(
            ChatMessage.session_id.in_(session_ids),
            ChatMessage.created_at < now - timedelta(days=settings.telegram_chat_retention_days),
        )
        .all()
    ]
    if message_ids:
        db.query(ChatTurn).filter(ChatTurn.message_id.in_(message_ids)).update(
            {ChatTurn.message_id: None}, synchronize_session=False
        )
        db.query(ChatMessage).filter(ChatMessage.id.in_(message_ids)).delete(synchronize_session=False)

    orphan_ids = [
        session_id
        for (session_id,) in db.query(ChatSession.id)
        .filter(ChatSession.id.in_(session_ids))
        .filter(~db.query(ChatMessage.id).filter(ChatMessage.session_id == ChatSession.id).exists())
        .filter(~db.query(TelegramLink.id).filter(TelegramLink.chat_session_id == ChatSession.id).exists())
        .all()
    ]
    if orphan_ids:
        db.query(ChatTurn).filter(ChatTurn.session_id.in_(orphan_ids)).delete(synchronize_session=False)
        db.query(ChatSession).filter(ChatSession.id.in_(orphan_ids)).delete(synchronize_session=False)
    db.flush()
    return {"messages": len(message_ids), "sessions": len(orphan_ids)}
