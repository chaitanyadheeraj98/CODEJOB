from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.ai.chat.agent import chat_models
from app.config import settings
from app.db import get_db
from app.runtime_state import runtime_state
from app.schemas import (
    ChatAttachmentResponse,
    ChatDeleteResponse,
    ChatMessageRequest,
    ChatMessageResponse,
    ChatSessionDetailResponse,
    ChatSessionRenameRequest,
    ChatSessionResponse,
    ChatStatusResponse,
    ProposalOutcomeRequest,
)
from app.services.chat_attachment_service import ChatAttachmentService
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


def require_chat_actions_enabled() -> None:
    require_chat_enabled()
    if not settings.feature_chat_actions_enabled:
        raise HTTPException(status_code=404, detail="Chat actions are disabled")


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
        model=runtime_state.chat_active_model or settings.ollama_chat_model,
        available_models=chat_models(),
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
    since_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionDetailResponse:
    session = service._session_or_404(db, session_id)
    messages = service.get_session_messages(db, session_id, since_id=since_id)
    return ChatSessionDetailResponse(
        **ChatSessionResponse.model_validate(session).model_dump(),
        messages=[ChatMessageResponse.model_validate(row) for row in messages],
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=ChatSessionResponse,
    dependencies=[Depends(require_chat_enabled)],
)
def rename_chat_session(
    session_id: int,
    payload: ChatSessionRenameRequest,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatSessionResponse:
    return ChatSessionResponse.model_validate(service.rename_session(db, session_id, payload.title))


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
        service.send_message(
            db, session_id, text, model=payload.model, attachment_ids=payload.attachment_ids
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/sessions/{session_id}/events",
    response_model=ChatMessageResponse,
    status_code=201,
    dependencies=[Depends(require_chat_actions_enabled)],
)
def record_proposal_outcome_route(
    session_id: int,
    payload: ProposalOutcomeRequest,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatMessageResponse:
    """Record that a proposal card was confirmed, cancelled or failed.

    Called by the dashboard after the click, never by the model. It is what lets
    a later turn tell a save that happened from one that did not.
    """
    return ChatMessageResponse.model_validate(
        service.record_proposal_outcome(
            db,
            session_id,
            tool_name=payload.tool_name,
            outcome=payload.outcome,
            proposal_message_id=payload.proposal_message_id,
            characters=payload.characters,
        )
    )


@router.post(
    "/sessions/{session_id}/attachments",
    response_model=ChatAttachmentResponse,
    status_code=201,
    dependencies=[Depends(require_chat_enabled)],
)
def upload_chat_attachment(
    session_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> ChatAttachmentResponse:
    service._session_or_404(db, session_id)
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name required")
    row = ChatAttachmentService.create(db, session_id, file.filename, file.content_type, file.file)
    return ChatAttachmentResponse.model_validate(row)


@router.get(
    "/sessions/{session_id}/attachments",
    response_model=list[ChatAttachmentResponse],
    dependencies=[Depends(require_chat_enabled)],
)
def list_chat_attachments_route(
    session_id: int,
    db: Session = Depends(get_db),
    service: ChatService = Depends(get_chat_service),
) -> list[ChatAttachmentResponse]:
    service._session_or_404(db, session_id)
    return [
        ChatAttachmentResponse.model_validate(row)
        for row in ChatAttachmentService.list_for_session(db, session_id)
    ]


@router.get(
    "/attachments/{attachment_id}/download",
    dependencies=[Depends(require_chat_enabled)],
)
def download_chat_attachment(attachment_id: int, db: Session = Depends(get_db)) -> FileResponse:
    row = ChatAttachmentService.get(db, attachment_id)
    if not Path(row.file_path).exists():
        raise HTTPException(status_code=410, detail="Attachment file is no longer on disk")
    return FileResponse(row.file_path, media_type=row.mime_type, filename=row.file_name)


@router.delete(
    "/attachments/{attachment_id}",
    response_model=ChatDeleteResponse,
    dependencies=[Depends(require_chat_enabled)],
)
def delete_chat_attachment(attachment_id: int, db: Session = Depends(get_db)) -> ChatDeleteResponse:
    ChatAttachmentService.delete(db, attachment_id)
    return ChatDeleteResponse(id=attachment_id, deleted=True)
