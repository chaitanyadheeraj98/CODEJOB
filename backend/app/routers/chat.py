from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.runtime_state import runtime_state
from app.schemas import (
    ChatDeleteResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatSessionDetailResponse,
    ChatSessionResponse,
    ChatStatusResponse,
)
from app.services.chat_service import ChatService


router = APIRouter(prefix="/chat", tags=["chat"])
_chat_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


def require_chat_enabled() -> None:
    if not settings.feature_chat_enabled:
        raise HTTPException(status_code=404, detail="Chat is disabled")


async def _ollama_running() -> bool:
    started = perf_counter()
    runtime_state.ollama_last_attempted_at = datetime.now(UTC)
    try:
        timeout = max(0.25, min(settings.ollama_timeout_seconds, 3.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
            response.raise_for_status()
        runtime_state.ollama_last_error = None
        runtime_state.ollama_last_success_at = datetime.now(UTC)
        return True
    except Exception as exc:
        runtime_state.ollama_last_error = str(exc)[:2000]
        return False
    finally:
        runtime_state.ollama_last_duration_ms = max(0, int((perf_counter() - started) * 1000))


@router.get("/status", response_model=ChatStatusResponse)
async def chat_status() -> ChatStatusResponse:
    running = await _ollama_running() if settings.feature_chat_enabled else False
    return ChatStatusResponse(
        enabled=settings.feature_chat_enabled,
        ollama_running=running,
        ollama_last_error=runtime_state.ollama_last_error,
        ollama_last_success_at=runtime_state.ollama_last_success_at,
        chat_last_error=runtime_state.chat_last_error,
        mcp_status=runtime_state.chat_mcp_status,
        model=settings.ollama_chat_model,
    )


@router.post(
    "/sessions",
    response_model=ChatSessionResponse,
    dependencies=[Depends(require_chat_enabled)],
)
def create_chat_session(
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    return ChatSessionResponse.model_validate(service.create_session(db))


@router.get(
    "/sessions",
    response_model=list[ChatSessionResponse],
    dependencies=[Depends(require_chat_enabled)],
)
def list_chat_sessions(
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatSessionResponse]:
    return [ChatSessionResponse.model_validate(row) for row in service.list_sessions(db)]


@router.get(
    "/sessions/{session_id}",
    response_model=ChatSessionDetailResponse,
    dependencies=[Depends(require_chat_enabled)],
)
def get_chat_session(
    session_id: int,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionDetailResponse:
    session = service._session_or_404(db, session_id)
    messages = service.get_session_messages(db, session_id)
    return ChatSessionDetailResponse(
        **ChatSessionResponse.model_validate(session).model_dump(),
        messages=[ChatMessageResponse.model_validate(row) for row in messages],
    )


@router.delete(
    "/sessions/{session_id}",
    response_model=ChatDeleteResponse,
    dependencies=[Depends(require_chat_enabled)],
)
def delete_chat_session(
    session_id: int,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatDeleteResponse:
    service.delete_session(db, session_id)
    return ChatDeleteResponse(id=session_id, deleted=True)


@router.post(
    "/sessions/{session_id}/messages",
    dependencies=[Depends(require_chat_enabled)],
)
def send_chat_message(
    session_id: int,
    payload: ChatMessageRequest,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> StreamingResponse:
    text = service.validate_message(payload.text)
    service._session_or_404(db, session_id)
    return StreamingResponse(
        service.send_message(db, session_id, text),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
