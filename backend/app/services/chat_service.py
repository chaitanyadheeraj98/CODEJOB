from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sqlalchemy.orm import Session

from app.ai.chat import agent as chat_agent
from app.ai.chat import turns
from app.ai.chat.history import db_messages_to_langchain, langchain_message_to_db_row, message_text
from app.ai.chat.system_prompt import prompt_sha256
from app.config import settings
from app.models import ChatMessage, ChatSession, ChatTurn, UserSettings
from app.services.chat_attachment_service import ChatAttachmentService


def _sse(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


class ChatService:
    @staticmethod
    def _record_turn(db: Session, **values) -> ChatTurn | None:
        try:
            row = ChatTurn(**values)
            db.add(row)
            db.commit()
            return row
        except Exception:
            db.rollback()
            logging.getLogger(__name__).warning("Could not record chat turn telemetry", exc_info=True)
            return None

    # Bounded on purpose. A summary that scans the whole table gets slower every
    # week and is read from /chat/status, which the dashboard polls - the point of
    # the window and the row cap is that this cost cannot grow.
    TELEMETRY_WINDOW_DAYS = 7
    TELEMETRY_MAX_ROWS = 500

    @classmethod
    def telemetry_summary(cls, db: Session) -> dict[str, object] | None:
        """Aggregate the recent chat_turn rows, or None if there is nothing to say.

        Wrapped and swallowed for the same reason the write is: telemetry that can
        break the status card would be reporting on an outage it caused.
        """
        try:
            since = datetime.now(UTC) - timedelta(days=cls.TELEMETRY_WINDOW_DAYS)
            rows = (
                db.query(
                    ChatTurn.duration_ms, ChatTurn.prompt_tokens, ChatTurn.completion_tokens,
                    ChatTurn.failure_code, ChatTurn.failed_over, ChatTurn.cancelled, ChatTurn.interrupted,
                )
                .filter(ChatTurn.created_at >= since)
                .order_by(ChatTurn.created_at.desc())
                .limit(cls.TELEMETRY_MAX_ROWS)
                .all()
            )
            if not rows:
                return None
            durations = sorted(row.duration_ms or 0 for row in rows)
            codes = Counter(row.failure_code for row in rows if row.failure_code)
            # Index, not interpolation: with 500 rows at most this is the honest
            # "95% of turns were at least this fast" and needs no numpy.
            p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))]
            return {
                "window_days": cls.TELEMETRY_WINDOW_DAYS,
                "turns": len(rows),
                "failed": sum(1 for row in rows if row.failure_code),
                "cancelled": sum(1 for row in rows if row.cancelled),
                # A cancelled turn is also interrupted; counting it in both would
                # read as two problems where the user pressed one button.
                "interrupted": sum(1 for row in rows if row.interrupted and not row.cancelled),
                "failed_over": sum(1 for row in rows if row.failed_over),
                "prompt_tokens": sum(row.prompt_tokens or 0 for row in rows),
                "completion_tokens": sum(row.completion_tokens or 0 for row in rows),
                "median_duration_ms": durations[len(durations) // 2],
                "p95_duration_ms": p95,
                "top_failure_code": codes.most_common(1)[0][0] if codes else None,
            }
        except Exception:
            logging.getLogger(__name__).warning("Could not summarise chat turn telemetry", exc_info=True)
            return None

    @classmethod
    def _attach_answering_model(cls, db: Session, rows: list[ChatMessage]) -> None:
        """Mark assistant rows whose answer came from a fallback model.

        Only failed-over turns are marked. Every turn has a model, but naming it
        on all of them would put a label on 100% of messages carrying information
        about ~0% of them; the fact worth surfacing is that the first choice did
        not answer this one.
        """
        ids = [row.id for row in rows if row.role == "assistant"]
        if not ids:
            return
        answered = {
            turn.message_id: turn.model
            for turn in db.query(ChatTurn.message_id, ChatTurn.model)
            .filter(ChatTurn.message_id.in_(ids), ChatTurn.failed_over.is_(True))
            .all()
            if turn.model
        }
        for row in rows:
            # Unmapped attribute, read by ChatMessageResponse through
            # from_attributes and defaulted to None where it was never set.
            row.answered_by = answered.get(row.id)

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
        found = rows.order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc()).all()
        self._attach_answering_model(db, found)
        return found

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
        db.query(ChatTurn).filter(ChatTurn.session_id == session_id).delete(synchronize_session=False)
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

    async def send_message(self, db: Session, session_id: int, user_text: str,
                           model: str | None = None, attachment_ids: list[int] | None = None) -> AsyncIterator[str]:
        self.validate_message(user_text)
        self._session_or_404(db, session_id)
        if not turns.slots.acquire(blocking=False):
            self._record_turn(db, session_id=session_id, message_id=None, requested_model=model or "auto",
                              failure_code="admission_rejected", prompt_sha256=prompt_sha256())
            raise HTTPException(status_code=503, detail="The assistant is handling other turns. Try again in a moment.")
        try:
            async with aclosing(self._send_message(db, session_id, user_text, model, attachment_ids)) as stream:
                async for event in stream:
                    yield event
        finally:
            turns.slots.release()

    async def _send_message(
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
        needs_title = not session.title
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
        assistant = ChatMessage(
            session_id=session.id, role="assistant", content="",
            tool_call_args='{"interrupted":true}',
        )
        db.add(assistant)
        db.commit()
        streamed_text = ""
        generated: list[BaseMessage] = []
        persisted_tools: set[str] = set()
        last_write = perf_counter()
        started = last_write
        metrics: dict = {}
        failure = None
        finished = False
        completed = False
        turn_id = str(uuid4())
        turn = turns.start(session.id, turn_id, chat_agent.stream_chat_agent(
            history, model=model, candidate_profile=self._candidate_profile(db)
        ))
        try:
            yield _sse("start", {"message_id": assistant.id, "turn_id": turn_id})
            async with aclosing(turns.events(turn)) as stream:
                async for kind, payload in stream:
                    if kind == "telemetry" and isinstance(payload, dict):
                        metrics = payload
                    elif kind == "error" and isinstance(payload, dict):
                        failure = payload.get("code")
                        yield _sse("error", payload)
                    elif kind == "delta":
                        delta = str(payload)
                        streamed_text += delta
                        if perf_counter() - last_write >= 2:
                            assistant.content = streamed_text
                            db.commit()
                            last_write = perf_counter()
                        yield _sse("message", {"delta": delta})
                    elif kind == "tool":
                        yield _sse("tool", {"name": str(payload)})
                    elif kind == "tool_done" and isinstance(payload, dict):
                        # Progress only, like "tool" above: nothing here is
                        # persisted or replayed into the model's history.
                        yield _sse("tool_done", payload)
                    elif kind in {"values", "complete"} and isinstance(payload, list):
                        completed = completed or kind == "complete"
                        generated = payload
                        for message in generated:
                            if isinstance(message, ToolMessage) and message.tool_call_id not in persisted_tools:
                                row = langchain_message_to_db_row(session.id, message)
                                db.add(row)
                                db.commit()
                                persisted_tools.add(message.tool_call_id)
            final_text = next(
                (message_text(message.content) for message in reversed(generated)
                 if isinstance(message, AIMessage) and message_text(message.content)),
                streamed_text,
            )
            if not streamed_text and final_text:
                streamed_text = final_text
                yield _sse("message", {"delta": final_text})
            assistant.content = final_text or streamed_text
            tool_calls = [call for message in generated if isinstance(message, AIMessage)
                          for call in (getattr(message, "tool_calls", None) or [])]
            if not failure and not turn.cancelled and completed:
                assistant.tool_call_args = json.dumps(tool_calls, separators=(",", ":")) if tool_calls else None
                finished = True
        finally:
            await turns.close(turn_id)
            if not finished:
                assistant.content = streamed_text
                db.add(ChatMessage(
                    session_id=session.id, role="event",
                    content=("[System: the user stopped the assistant turn. Its partial answer is incomplete.]" if turn.cancelled
                             else "[System: the assistant turn was interrupted. Its partial answer is incomplete.]"),
                ))
            session.updated_at = datetime.now(UTC)
            db.commit()
            self._record_turn(db,
                    session_id=session.id, message_id=assistant.id, requested_model=model or "auto",
                    **{**metrics, "tool_calls": json.dumps(metrics.get("tool_calls", [])),
                       "prompt_sha256": metrics.get("prompt_sha256", prompt_sha256()),
                       "duration_ms": int((perf_counter() - started) * 1000),
                       "failure_code": failure or metrics.get("failure_code"),
                       "interrupted": not finished, "cancelled": turn.cancelled},
            )
        yield _sse("done", {"message_id": assistant.id, "cancelled": turn.cancelled})
        if finished and needs_title and settings.feature_chat_title_generation:
            from app.ai.chat.tasks import schedule_title

            schedule_title(session.id, assistant.id, text, text[:80])
