from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy.orm import Session

from app.ai.chat import agent as chat_agent
from app.ai.chat.history import db_messages_to_langchain, langchain_message_to_db_row, message_text
from app.config import settings
from app.models import ChatMessage, ChatSession
from app.services.chat_attachment_service import ChatAttachmentService


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


class ChatService:
    @staticmethod
    def _session_or_404(db: Session, session_id: int) -> ChatSession:
        row = (
            db.query(ChatSession)
            .filter(ChatSession.owner_id == settings.owner_id, ChatSession.id == session_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Chat session not found")
        return row

    @staticmethod
    def validate_message(text: str) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Message text is required")
        if len(cleaned) > settings.chat_message_char_limit:
            raise HTTPException(
                status_code=422,
                detail=f"Message exceeds the {settings.chat_message_char_limit}-character limit",
            )
        return cleaned

    def create_session(self, db: Session) -> ChatSession:
        row = ChatSession(owner_id=settings.owner_id)
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def list_sessions(self, db: Session) -> list[ChatSession]:
        return (
            db.query(ChatSession)
            .filter(ChatSession.owner_id == settings.owner_id)
            .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
            .all()
        )

    def get_session_messages(
        self, db: Session, session_id: int, since_id: int | None = None
    ) -> list[ChatMessage]:
        """Messages for a session, oldest first.

        `since_id` returns only messages newer than that id, so the dashboard's
        background poll can ask for the delta instead of re-downloading the whole
        thread every 20 seconds.
        """
        self._session_or_404(db, session_id)
        rows = db.query(ChatMessage).filter(ChatMessage.session_id == session_id)
        if since_id is not None:
            rows = rows.filter(ChatMessage.id > since_id)
        return rows.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc()).all()

    def rename_session(self, db: Session, session_id: int, title: str) -> ChatSession:
        cleaned = title.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Title is required")
        row = self._session_or_404(db, session_id)
        row.title = cleaned[:120]
        db.commit()
        db.refresh(row)
        return row

    def delete_session(self, db: Session, session_id: int) -> None:
        row = self._session_or_404(db, session_id)
        # Attachments first: they hold foreign keys to both the messages and the
        # session, and they own files on disk that nothing else would clean up.
        for attachment in ChatAttachmentService.list_for_session(db, session_id):
            ChatAttachmentService.delete(db, attachment.id)
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete(synchronize_session=False)
        db.delete(row)
        db.commit()

    async def send_message(
        self,
        db: Session,
        session_id: int,
        user_text: str,
        model: str | None = None,
        attachment_ids: list[int] | None = None,
    ) -> AsyncIterator[str]:
        text = self.validate_message(user_text)
        session = self._session_or_404(db, session_id)
        now = datetime.now(UTC)
        if not session.title:
            session.title = text[:80]
        session.updated_at = now
        user_message = ChatMessage(session_id=session.id, role="user", content=text, created_at=now)
        db.add(user_message)
        db.flush()

        # The note is appended *after* validate_message, not by the client.
        # Three long filenames would otherwise eat the user's 4000-character
        # budget and could push a legitimate message over the limit.
        attached = ChatAttachmentService.bind_to_message(
            db, session.id, user_message.id, list(attachment_ids or [])
        )
        if attached:
            user_message.content = text + ChatAttachmentService.note_for(attached)
        db.commit()

        recent = (
            db.query(ChatMessage)
            .filter(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.id.desc())
            .limit(max(1, settings.chat_history_max_messages))
            .all()
        )
        history = db_messages_to_langchain(list(reversed(recent)))
        streamed_text = ""
        generated: list[BaseMessage] = []
        async for kind, payload in chat_agent.stream_chat_agent(history, model=model):
            if kind == "delta":
                delta = str(payload)
                streamed_text += delta
                yield _sse("message", {"delta": delta})
            elif kind == "complete" and isinstance(payload, list):
                generated = payload

        final_text = next(
            (
                message_text(message.content)
                for message in reversed(generated)
                if isinstance(message, AIMessage) and message_text(message.content)
            ),
            streamed_text,
        )
        if not streamed_text and final_text:
            yield _sse("message", {"delta": final_text})

        for message in generated:
            if isinstance(message, ToolMessage):
                row = langchain_message_to_db_row(session.id, message)
                if row is not None:
                    db.add(row)
        tool_calls = [
            call
            for message in generated
            if isinstance(message, AIMessage)
            for call in (getattr(message, "tool_calls", None) or [])
        ]
        assistant = ChatMessage(
            session_id=session.id,
            role="assistant",
            content=final_text or streamed_text,
            tool_call_args=json.dumps(tool_calls, separators=(",", ":")) if tool_calls else None,
        )
        db.add(assistant)
        session.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(assistant)
        yield _sse("done", {"message_id": assistant.id})
