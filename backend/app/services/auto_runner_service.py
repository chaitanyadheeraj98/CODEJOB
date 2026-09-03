from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Event, Lock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import UserSettings

logger = logging.getLogger(__name__)

LIVE_REPLY_CHECK_INTERVAL_SECONDS = 60


class AutoRunnerService:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        get_settings: Callable[[Session], UserSettings],
        run_once: Callable[[object | None, Session], object],
        run_nvoids_once: Callable[[Session, int], object],
        check_live_replies: Callable[[Session], None],
        run_reminder_sweep: Callable[[Session], None],
        run_resume_tracking_sweep: Callable[[Session], None],
        # Defaults to a no-op so an existing construction site keeps working;
        # the feature flag gates the sweep either way.
        run_relationship_sweep: Callable[[Session], None] = lambda _db: None,
        action_lock: Lock,
        stop_event: Event,
    ) -> None:
        self._session_factory = session_factory
        self._get_settings = get_settings
        self._run_once = run_once
        self._run_nvoids_once = run_nvoids_once
        self._check_live_replies = check_live_replies
        self._run_reminder_sweep = run_reminder_sweep
        self._run_resume_tracking_sweep = run_resume_tracking_sweep
        self._run_relationship_sweep = run_relationship_sweep
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

    @staticmethod
    def reminder_sweep_interval_minutes(user_settings: UserSettings) -> int:
        return max(30, min(int(user_settings.feature_reminder_sweep_interval_minutes or 240), 1440))

    @staticmethod
    def resume_tracking_sweep_interval_minutes(user_settings: UserSettings) -> int:
        return max(30, min(int(user_settings.feature_resume_tracking_sweep_interval_minutes or 240), 1440))

    @staticmethod
    def relationship_sweep_interval_minutes() -> int:
        # Clamped exactly as its neighbours are. Read from config rather than
        # UserSettings: the feature's master switch is env-only, so a
        # runtime-tunable cadence for it would buy nothing.
        return max(30, min(int(settings.feature_relationship_sweep_interval_minutes or 720), 1440))

    def run_loop(self) -> None:
        next_run_at = datetime.now(UTC)
        next_nvoids_run_at = datetime.now(UTC)
        next_live_check_at = datetime.now(UTC)
        next_reminder_sweep_at = datetime.now(UTC)
        next_resume_tracking_sweep_at = datetime.now(UTC)
        next_relationship_sweep_at = datetime.now(UTC)
        while not self._stop_event.wait(5):
            db = self._session_factory()
            try:
                user_settings = self._get_settings(db)
                if not user_settings.enabled or not user_settings.feature_auto_polling:
                    next_run_at = datetime.now(UTC)
                now_utc = datetime.now(UTC)

                # Runs on its own cadence, outside action_lock, so a live count is
                # visible even while a full sync is in progress under that lock.
                if user_settings.enabled and now_utc >= next_live_check_at:
                    try:
                        self._check_live_replies(db)
                    except Exception:
                        logger.exception("Live reply check crashed")
                    next_live_check_at = datetime.now(UTC) + timedelta(seconds=LIVE_REPLY_CHECK_INTERVAL_SECONDS)

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

                if (
                    user_settings.enabled
                    and user_settings.feature_applications_enabled
                    and user_settings.feature_application_automation_enabled
                    and now_utc >= next_reminder_sweep_at
                ):
                    reminder_interval_minutes = self.reminder_sweep_interval_minutes(user_settings)
                    with self._action_lock:
                        try:
                            self._run_reminder_sweep(db)
                        except Exception:
                            logger.exception("Reminder sweep crashed")
                    next_reminder_sweep_at = datetime.now(UTC) + timedelta(minutes=reminder_interval_minutes)

                if (
                    user_settings.enabled
                    and user_settings.feature_resume_tracking_enabled
                    and now_utc >= next_resume_tracking_sweep_at
                ):
                    resume_tracking_interval_minutes = self.resume_tracking_sweep_interval_minutes(user_settings)
                    with self._action_lock:
                        try:
                            self._run_resume_tracking_sweep(db)
                        except Exception:
                            logger.exception("Resume tracking sweep crashed")
                    next_resume_tracking_sweep_at = datetime.now(UTC) + timedelta(
                        minutes=resume_tracking_interval_minutes
                    )

                # Entity embedding top-up, then one clustering pass. Both are
                # idempotent and resumable, both take the action lock as their
                # neighbours do, and the clustering pass writes shadow rows
                # unless surfacing has been explicitly enabled and the
                # thresholds calibrated.
                if (
                    user_settings.enabled
                    and settings.feature_relationship_intelligence_enabled
                    and now_utc >= next_relationship_sweep_at
                ):
                    relationship_interval_minutes = self.relationship_sweep_interval_minutes()
                    with self._action_lock:
                        try:
                            self._run_relationship_sweep(db)
                        except Exception:
                            logger.exception("Relationship sweep crashed")
                    next_relationship_sweep_at = datetime.now(UTC) + timedelta(
                        minutes=relationship_interval_minutes
                    )
            except Exception:
                logger.exception("Auto runner loop error")
            finally:
                db.close()
