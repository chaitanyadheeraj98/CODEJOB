from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import TelegramLink, utc_now
from app.services.telegram_runtime import TelegramRuntimeState

LINK_TTL_MINUTES = 10
_START_CODE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PIN = re.compile(r"^\d{4,6}$")


@dataclass(frozen=True)
class TelegramLinkStatus:
    linked: bool
    chat_id: int | None = None
    telegram_username: str = ""
    linked_at: datetime | None = None
    alerts_enabled: bool = True
    pin_set: bool = False
    pending_code_expires_at: datetime | None = None


def _row(db: Session, owner_id: str) -> TelegramLink | None:
    return db.query(TelegramLink).filter(TelegramLink.owner_id == owner_id).first()


def _get_or_create(db: Session, owner_id: str) -> TelegramLink:
    row = _row(db, owner_id)
    if row is None:
        row = TelegramLink(owner_id=owner_id)
        db.add(row)
    return row


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def issue_link_code(db: Session, owner_id: str) -> str:
    code = secrets.token_urlsafe(24)
    assert _START_CODE.fullmatch(code)
    row = _get_or_create(db, owner_id)
    row.link_code_hash = _code_hash(code)
    row.link_code_expires_at = utc_now() + timedelta(minutes=LINK_TTL_MINUTES)
    db.flush()
    return code


def redeem_link_code(
    db: Session,
    code: str,
    *,
    chat_id: int,
    telegram_user_id: str,
    username: str,
) -> str | None:
    if not _START_CODE.fullmatch(code):
        return None
    digest = _code_hash(code)
    row = db.query(TelegramLink).filter(TelegramLink.link_code_hash == digest).first()
    if row is None or not hmac.compare_digest(row.link_code_hash, digest):
        return None
    if row.link_code_expires_at is None or row.link_code_expires_at <= utc_now():
        return None
    conflict = db.query(TelegramLink).filter(TelegramLink.chat_id == chat_id).first()
    if conflict is not None and conflict.owner_id != row.owner_id:
        return None
    old_chat_id = row.chat_id
    row.chat_id = chat_id
    row.telegram_user_id = telegram_user_id[:40]
    row.telegram_username = username[:64]
    row.link_code_hash = ""
    row.link_code_expires_at = None
    row.linked_at = utc_now()
    row.last_seen_at = row.linked_at
    db.flush()
    if old_chat_id is not None and old_chat_id != chat_id:
        TelegramRuntimeState.clear_session(old_chat_id)
        TelegramRuntimeState.clear_pending_mode(old_chat_id)
    return row.owner_id


def resolve_owner(db: Session, chat_id: int) -> str | None:
    row = db.query(TelegramLink.owner_id).filter(TelegramLink.chat_id == chat_id).first()
    return row[0] if row else None


def unlink(db: Session, owner_id: str) -> bool:
    row = _row(db, owner_id)
    if row is None or row.chat_id is None:
        return False
    chat_id = row.chat_id
    row.chat_id = None
    row.telegram_user_id = ""
    row.telegram_username = ""
    row.link_code_hash = ""
    row.link_code_expires_at = None
    row.linked_at = None
    row.last_seen_at = None
    db.flush()
    TelegramRuntimeState.clear_session(chat_id)
    TelegramRuntimeState.clear_pending_mode(chat_id)
    return True


def link_status(db: Session, owner_id: str) -> TelegramLinkStatus:
    row = _row(db, owner_id)
    if row is None:
        return TelegramLinkStatus(linked=False)
    return TelegramLinkStatus(
        linked=row.chat_id is not None,
        chat_id=row.chat_id,
        telegram_username=row.telegram_username or "",
        linked_at=row.linked_at,
        alerts_enabled=row.alerts_enabled,
        pin_set=bool(row.action_pin_hash),
        pending_code_expires_at=row.link_code_expires_at,
    )


def _pin_hash(pin: str, salt: bytes) -> str:
    digest = hashlib.scrypt(pin.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"{salt.hex()}:{digest.hex()}"


def set_action_pin(db: Session, owner_id: str, pin: str) -> None:
    pin = pin.strip()
    row = _get_or_create(db, owner_id)
    if not pin:
        row.action_pin_hash = ""
    else:
        if not _PIN.fullmatch(pin):
            raise ValueError("Action PIN must contain 4 to 6 digits.")
        row.action_pin_hash = _pin_hash(pin, secrets.token_bytes(16))
    db.flush()


def verify_action_pin(db: Session, owner_id: str, pin: str) -> bool:
    row = _row(db, owner_id)
    if row is None or not row.action_pin_hash:
        return True
    try:
        salt_hex, expected = row.action_pin_hash.split(":", 1)
        actual = _pin_hash(pin.strip(), bytes.fromhex(salt_hex)).split(":", 1)[1]
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def set_alerts_enabled(db: Session, owner_id: str, enabled: bool) -> None:
    _get_or_create(db, owner_id).alerts_enabled = enabled
    db.flush()


def chat_ids_for_owner(db: Session, owner_id: str) -> list[int]:
    rows = (
        db.query(TelegramLink.chat_id)
        .filter(
            TelegramLink.owner_id == owner_id,
            TelegramLink.chat_id.is_not(None),
            TelegramLink.alerts_enabled.is_(True),
        )
        .all()
    )
    return [row[0] for row in rows]


def touch_last_seen(db: Session, chat_id: int) -> None:
    row = db.query(TelegramLink).filter(TelegramLink.chat_id == chat_id).first()
    if row is not None:
        row.last_seen_at = utc_now()
        db.flush()
