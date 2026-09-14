from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.gmail_labeling import GmailLabelingService
from app.shared_status import SHARED_FIELDS, shared_status
from app.telegram_bot import TelegramBotService

# Defaults for the shared telemetry fields. They live here rather than as class
# attributes because those names are intercepted before Python ever looks at the
# class - see `__getattr__` below.
_SHARED_DEFAULTS: dict[str, Any] = {
    "last_gmail_sync_at": None,
    "ai_running": False,
    "ai_last_error": None,
    "ai_last_started_at": None,
    "ai_last_finished_at": None,
    "ai_last_duration_ms": None,
    "ai_last_draft_source": None,
    "embedding_last_error": None,
    "embedding_last_attempted_at": None,
    "embedding_last_success_at": None,
    "embedding_last_duration_ms": None,
    "groq_last_error": None,
    "groq_last_attempted_at": None,
    "groq_last_success_at": None,
    "groq_last_duration_ms": None,
    "groq_last_provider_result": None,
    "groq_request_mode": "",
    "intent_gate_provider": "",
    "intent_gate_last_error": None,
    "intent_gate_last_attempted_at": None,
    "intent_gate_last_success_at": None,
    "intent_gate_last_duration_ms": None,
    "intent_gate_last_provider_result": None,
    "intent_gate_last_rung": "",
    "intent_gate_last_escalated": False,
    "ollama_last_error": None,
    "ollama_last_attempted_at": None,
    "ollama_last_success_at": None,
    "ollama_last_duration_ms": None,
    "chat_last_error": None,
    "chat_last_failure_code": None,
    "chat_last_attempted_at": None,
    "chat_last_success_at": None,
    "chat_last_duration_ms": None,
    "chat_mcp_status": "disabled",
    "chat_active_model": None,
    "live_replies": {},
}


class AppRuntimeState:
    """Runtime state, split by what can meaningfully be shared.

    **Telemetry is shared.** Every field in `SHARED_FIELDS` is read from and
    written to Redis, so four uvicorn workers answer `/gmail/status`,
    `/ai/status` and `/gmail/live-replies` identically instead of reporting
    whichever worker happened to take the request. Leader election made that
    sharper rather than better: the auto-runner runs in exactly one process, so
    only that process would ever have live-reply data.

    **Handles are not.** Threads, locks, stop events and service objects stay
    ordinary attributes, because they *are* this process - a `threading.Event`
    in Redis would be a description of an event rather than one.

    The interception is here rather than at the call sites so that all hundred
    of them read the same as they always did. A per-site rewrite would be a
    hundred chances to miss one, and a missed one is a status card that is
    quietly wrong on three workers out of four.
    """

    def __init__(self) -> None:
        self.telegram_service: TelegramBotService | None = None
        # Still process-local, and correct that way: its only remaining user is
        # the auto-runner, which runs in the elected leader and nowhere else, so
        # one process is the whole population. The endpoint that used to share
        # it holds a `distributed_lock` instead, and the auto-runner's other
        # sweeps enqueue rather than run inline - where the queue already
        # rejects a second job.
        self.telegram_action_lock: threading.Lock = threading.Lock()
        # Multi-step Telegram flows. Process-local is fine now that only the
        # leader polls Telegram, so one process sees every update in a flow.
        self.telegram_auth_sessions: dict[int, datetime] = {}
        self.telegram_pending_inputs: dict[int, str] = {}
        self.auto_runner_thread: threading.Thread | None = None
        self.auto_runner_stop_event: threading.Event = threading.Event()
        self.gmail_labeling_service: GmailLabelingService | None = None

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not set in __init__, which is exactly the
        # shared set - Python calls this after the normal lookup fails.
        if name in SHARED_FIELDS:
            return shared_status.get(name, _SHARED_DEFAULTS.get(name))
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in SHARED_FIELDS:
            shared_status.set(name, value)
            return
        object.__setattr__(self, name, value)


runtime_state = AppRuntimeState()
