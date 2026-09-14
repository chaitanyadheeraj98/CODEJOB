from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import tenancy
from app.db import get_db
from app.runtime_state import runtime_state
from app.schemas import (
    TelegramLinkCodeResponse,
    TelegramLinkResponse,
    TelegramLinkSettingsRequest,
    TelegramUnlinkResponse,
)
from app.services import telegram_link_service

router = APIRouter(prefix="/telegram", tags=["telegram"])


def _bot_username() -> str:
    service = runtime_state.telegram_service
    return service.bot_username if service is not None else ""


def _response(db: Session, owner_id: str) -> TelegramLinkResponse:
    status = telegram_link_service.link_status(db, owner_id)
    return TelegramLinkResponse(
        linked=status.linked,
        chat_masked=f"…{str(status.chat_id)[-4:]}" if status.chat_id is not None else None,
        telegram_username=status.telegram_username,
        linked_at=status.linked_at,
        alerts_enabled=status.alerts_enabled,
        pin_set=status.pin_set,
        bot_username=_bot_username(),
        pending_code_expires_at=status.pending_code_expires_at,
    )


@router.get("/link", response_model=TelegramLinkResponse)
def get_link(db: Session = Depends(get_db)) -> TelegramLinkResponse:
    return _response(db, tenancy.owner_id())


@router.post("/link/code", response_model=TelegramLinkCodeResponse)
def create_link_code(db: Session = Depends(get_db)) -> TelegramLinkCodeResponse:
    username = _bot_username()
    if not username:
        raise HTTPException(status_code=503, detail="The CodeJob Telegram bot is not running.")
    code = telegram_link_service.issue_link_code(db, tenancy.owner_id())
    expires_at = telegram_link_service.link_status(db, tenancy.owner_id()).pending_code_expires_at
    db.commit()
    assert expires_at is not None
    return TelegramLinkCodeResponse(deep_link=f"https://t.me/{username}?start={code}", expires_at=expires_at)


@router.delete("/link", response_model=TelegramUnlinkResponse)
def delete_link(db: Session = Depends(get_db)) -> TelegramUnlinkResponse:
    telegram_link_service.unlink(db, tenancy.owner_id())
    db.commit()
    return TelegramUnlinkResponse()


@router.put("/link/settings", response_model=TelegramLinkResponse)
def update_link_settings(
    payload: TelegramLinkSettingsRequest,
    db: Session = Depends(get_db),
) -> TelegramLinkResponse:
    owner_id = tenancy.owner_id()
    if payload.alerts_enabled is not None:
        telegram_link_service.set_alerts_enabled(db, owner_id, payload.alerts_enabled)
    if payload.action_pin is not None:
        telegram_link_service.set_action_pin(db, owner_id, payload.action_pin)
    db.commit()
    return _response(db, owner_id)
