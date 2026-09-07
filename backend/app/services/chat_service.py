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
from app.models import ChatMessage, ChatSession, UserSettings
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
    def _candidate_profile(db: Session) -> str:
        """The user's own profile Markdown, read fresh on every turn.

        It goes into the system prompt rather than behind a tool: "write this as
        me" has to work whether or not the model chooses to look the user up, and
        a model that skips the lookup falls back to inventing the details.
        """
        row = (
            db.query(UserSettings.candidate_profile_markdown)
            .filter(UserSettings.owner_id == settings.owner_id)
            .first()
        )
        return (row[0] if row else "") or ""

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

    # The client sends an enumerated outcome and a number; every word below is
    # the server's. These rows are replayed into the model's history, which puts
    # them closer to trusted framing than to tool output - a free-text field
    # here would be a way to write into that position from the browser.
    _OUTCOME_SENTENCES = {
        "confirmed": "[System: your {tool} card was confirmed by the user.{detail}]",
        "cancelled": "[System: your {tool} card was cancelled by the user. Nothing was saved.]",
        "failed": "[System: your {tool} card failed. Nothing was saved.]",
    }

    def record_proposal_outcome(
        self,
        db: Session,
        session_id: int,
        *,
        tool_name: str,
        outcome: str,
        proposal_message_id: int,
        characters: int | None = None,
    ) -> ChatMessage:
        """Write what happened to a proposal card into the conversation.

        Without this the model's own transcript contains its claim - "I've saved
        your notice period" - and nothing that contradicts it, whichever button
        the user pressed: the proposal payload is a `tool` row and is never
        replayed, and the click's result is browser state that is never stored.
        Asking a model to be careful about a fact it has no access to is not a
        control, so the fact goes into the record.
        """
        session = self._session_or_404(db, session_id)
        template = self._OUTCOME_SENTENCES.get(outcome)
        if template is None:
            raise HTTPException(status_code=422, detail=f"Unknown outcome '{outcome}'")
        detail = ""
        if outcome == "confirmed" and characters is not None:
            detail = f" The Candidate Profile was saved and is now {characters:,} characters."
        row = ChatMessage(
            session_id=session.id,
            role="event",
            content=template.format(tool=tool_name, detail=detail),
            tool_name=tool_name,
            # Pairs the outcome back to its card after a reload, which is the
            # difference between the outcome being recorded and being usable.
            tool_call_args=json.dumps(
                {"proposal_message_id": int(proposal_message_id), "outcome": outcome},
                separators=(",", ":"),
            ),
        )
        db.add(row)
        session.updated_at = datetime.now(UTC)
        db.commit()
        db.refresh(row)
        return row

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
        async for kind, payload in chat_agent.stream_chat_agent(
            history, model=model, candidate_profile=self._candidate_profile(db)
        ):
            if kind == "delta":
                delta = str(payload)
                streamed_text += delta
                yield _sse("message", {"delta": delta})
            elif kind == "tool":
                # Progress only. Nothing here is persisted or replayed into the
                # model's history - it exists so the user can tell a working
                # assistant from a hung one during a long tool call.
                yield _sse("tool", {"name": str(payload)})
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
