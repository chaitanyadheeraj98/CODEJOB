from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.runtime_state import runtime_state


class TelegramRuntimeState:
    @staticmethod
    def session_expires_at(chat_id: int) -> datetime | None:
        expires_at = runtime_state.telegram_auth_sessions.get(chat_id)
        if expires_at and expires_at < datetime.now(UTC):
            runtime_state.telegram_auth_sessions.pop(chat_id, None)
            return None
        return expires_at

    @classmethod
    def session_is_active(cls, chat_id: int) -> bool:
        return cls.session_expires_at(chat_id) is not None

    @classmethod
    def session_remaining(cls, chat_id: int) -> str:
        expires_at = cls.session_expires_at(chat_id)
        if not expires_at:
            return "expired"
        remaining = max(0, int((expires_at - datetime.now(UTC)).total_seconds()))
        minutes = remaining // 60
        seconds = remaining % 60
        return f"{minutes}m {seconds}s"

    @staticmethod
    def activate_session(chat_id: int, ttl_minutes: int) -> None:
        runtime_state.telegram_auth_sessions[chat_id] = datetime.now(UTC) + timedelta(minutes=max(1, ttl_minutes))

    @staticmethod
    def clear_session(chat_id: int) -> None:
        runtime_state.telegram_auth_sessions.pop(chat_id, None)

    @staticmethod
    def get_pending_mode(chat_id: int) -> str | None:
        return runtime_state.telegram_pending_inputs.get(chat_id)

    @staticmethod
    def set_pending_mode(chat_id: int, mode: str) -> None:
        runtime_state.telegram_pending_inputs[chat_id] = mode

    @staticmethod
    def clear_pending_mode(chat_id: int) -> None:
        runtime_state.telegram_pending_inputs.pop(chat_id, None)
