from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ChatSession, TelegramLink


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
