from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Lock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import UserSettings
logger = logging.getLogger(__name__)


class AutoRunnerService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        get_settings: Callable[[Session], UserSettings],
        run_once: Callable[[object | None, Session], object],
        run_nvoids_once: Callable[[Session, int], object],
        action_lock: Lock,
        stop_event: Event,
    ) -> None:
        self._session_factory = session_factory
        self._get_settings = get_settings
        self._run_once = run_once
        self._run_nvoids_once = run_nvoids_once
        self._action_lock = action_lock
        self._stop_event = stop_event

    @staticmethod
    def poll_interval_minutes(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.feature_auto_poll_interval_minutes or 10), 1440))

    @staticmethod
    def nvoids_poll_interval_minutes(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.feature_nvoids_poll_interval_minutes or 30), 1440))

    @staticmethod
    def nvoids_batch_limit(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.nvoids_batch_limit or 10), 50))

    def run_loop(self) -> None:
        next_run_at = datetime.now(UTC)
        next_nvoids_run_at = datetime.now(UTC)
        while not self._stop_event.wait(5):
            db = self._session_factory()
            try:
                user_settings = self._get_settings(db)
                if not user_settings.enabled or not user_settings.feature_auto_polling:
                    next_run_at = datetime.now(UTC)
                now_utc = datetime.now(UTC)
                if user_settings.enabled and user_settings.feature_auto_polling and now_utc >= next_run_at:
                    interval_minutes = self.poll_interval_minutes(user_settings)
                    with self._action_lock:
                        try:
                            result = self._run_once(None, db)
                            logger.info(
                                "Auto runner enqueued: status=%s job_id=%s run_key=%s",
                                getattr(result, "status", None),
                                getattr(result, "job_id", None),
                                getattr(result, "run_key", None),
                            )
                        except HTTPException as exc:
                            logger.warning("Auto runner skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                        except Exception:
                            logger.exception("Auto runner crashed during run-once")
                    next_run_at = datetime.now(UTC) + timedelta(minutes=interval_minutes)

                if user_settings.enabled and user_settings.feature_nvoids_enabled and user_settings.feature_nvoids_auto_sync and now_utc >= next_nvoids_run_at:
                    nvoids_interval_minutes = self.nvoids_poll_interval_minutes(user_settings)
                    nvoids_limit = self.nvoids_batch_limit(user_settings)
                    with self._action_lock:
                        try:
                            result = self._run_nvoids_once(db, nvoids_limit)
                            logger.info(
                                "Auto nvoids sync enqueued: status=%s job_id=%s run_key=%s",
                                getattr(result, "status", None),
                                getattr(result, "job_id", None),
                                getattr(result, "run_key", None),
                            )
                        except HTTPException as exc:
                            logger.warning("Auto nvoids sync skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                        except Exception:
                            logger.exception("Auto nvoids sync crashed")
                    next_nvoids_run_at = datetime.now(UTC) + timedelta(minutes=nvoids_interval_minutes)
            except Exception:
                logger.exception("Auto runner loop error")
            finally:
                db.close()
