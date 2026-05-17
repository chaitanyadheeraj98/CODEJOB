from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Lock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import UserSettings
from app.schemas import AutomationRunResponse

logger = logging.getLogger(__name__)


class AutoRunnerService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        get_settings: Callable[[Session], UserSettings],
        run_once: Callable[[object | None, Session], AutomationRunResponse],
        action_lock: Lock,
        stop_event: Event,
    ) -> None:
        self._session_factory = session_factory
        self._get_settings = get_settings
        self._run_once = run_once
        self._action_lock = action_lock
        self._stop_event = stop_event

    @staticmethod
    def poll_interval_minutes(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.feature_auto_poll_interval_minutes or 10), 1440))

    def run_loop(self) -> None:
        next_run_at = datetime.now(UTC)
        while not self._stop_event.wait(5):
            db = self._session_factory()
            try:
                user_settings = self._get_settings(db)
                if not user_settings.enabled or not user_settings.feature_auto_polling:
                    next_run_at = datetime.now(UTC)
                    continue
                interval_minutes = self.poll_interval_minutes(user_settings)
                now_utc = datetime.now(UTC)
                if now_utc < next_run_at:
                    continue
                with self._action_lock:
                    try:
                        result = self._run_once(None, db)
                        logger.info(
                            "Auto runner completed: status=%s matched=%s queued=%s failed=%s",
                            result.status,
                            result.matched_count,
                            result.queued_count,
                            result.failed_count,
                        )
                    except HTTPException as exc:
                        logger.warning("Auto runner skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                    except Exception:
                        logger.exception("Auto runner crashed during run-once")
                next_run_at = datetime.now(UTC) + timedelta(minutes=interval_minutes)
            except Exception:
                logger.exception("Auto runner loop error")
            finally:
                db.close()
