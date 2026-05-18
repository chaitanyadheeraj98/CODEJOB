from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from app.db import Base, engine
from app.gmail_client import is_gmail_configured
from app.runtime_state import runtime_state
from app.services.migration_runtime_service import MigrationRuntimeService

logger = logging.getLogger(__name__)


class StartupService:
    def __init__(
        self,
        *,
        ensure_default_settings: Callable[[], None],
        ensure_labeling_service: Callable[[], object],
        init_telegram_service: Callable[[], object | None],
        auto_runner_loop: Callable[[], None],
    ) -> None:
        self._ensure_default_settings = ensure_default_settings
        self._ensure_labeling_service = ensure_labeling_service
        self._init_telegram_service = init_telegram_service
        self._auto_runner_loop = auto_runner_loop

    def startup(self) -> None:
        Base.metadata.create_all(bind=engine)
        MigrationRuntimeService().ensure_schema_ready()
        self._ensure_default_settings()
        self._ensure_labeling_service()
        if is_gmail_configured():
            try:
                runtime_state.gmail_labeling_service.ensure_target_labels()
            except Exception as exc:
                logger.warning("gmail_labeling startup_sync_failed error=%s", exc)
        runtime_state.telegram_service = self._init_telegram_service()
        runtime_state.auto_runner_stop_event.clear()
        runtime_state.auto_runner_thread = threading.Thread(
            target=self._auto_runner_loop, name="mailops-auto-runner", daemon=True
        )
        runtime_state.auto_runner_thread.start()

    def shutdown(self) -> None:
        runtime_state.auto_runner_stop_event.set()
        if runtime_state.auto_runner_thread and runtime_state.auto_runner_thread.is_alive():
            runtime_state.auto_runner_thread.join(timeout=5.0)
        if runtime_state.telegram_service:
            runtime_state.telegram_service.stop()
