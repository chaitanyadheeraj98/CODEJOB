from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import threading

from app.gmail_labeling import GmailLabelingService
from app.telegram_bot import TelegramBotService


@dataclass
class AppRuntimeState:
    last_gmail_sync_at: datetime | None = None
    ai_running: bool = False
    ai_last_error: str | None = None
    ai_last_started_at: datetime | None = None
    ai_last_finished_at: datetime | None = None
    ai_last_duration_ms: int | None = None
    ai_last_draft_source: str | None = None
    embedding_last_error: str | None = None
    embedding_last_attempted_at: datetime | None = None
    embedding_last_success_at: datetime | None = None
    embedding_last_duration_ms: int | None = None
    taxonomy_embedding_lock: threading.Lock = field(default_factory=threading.Lock)
    groq_last_error: str | None = None
    groq_last_attempted_at: datetime | None = None
    groq_last_success_at: datetime | None = None
    groq_last_duration_ms: int | None = None
    groq_last_provider_result: str | None = None
    groq_request_mode: str = ""
    telegram_service: TelegramBotService | None = None
    telegram_action_lock: threading.Lock = field(default_factory=threading.Lock)
    telegram_auth_sessions: dict[int, datetime] = field(default_factory=dict)
    telegram_pending_inputs: dict[int, str] = field(default_factory=dict)
    auto_runner_thread: threading.Thread | None = None
    auto_runner_stop_event: threading.Event = field(default_factory=threading.Event)
    gmail_labeling_service: GmailLabelingService | None = None


runtime_state = AppRuntimeState()
