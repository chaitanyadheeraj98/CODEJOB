from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event, Lock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app import tenancy
from app.config import settings
from app.models import UserSettings

logger = logging.getLogger(__name__)

LIVE_REPLY_CHECK_INTERVAL_SECONDS = 60


@dataclass
class _Schedule:
    """When each periodic job is next due, for one tenant.

    These were seven locals in `run_loop`, which was correct while the loop
    served exactly one owner. Per tenant now, so one account's sync cadence
    cannot reset another's.
    """

    next_run_at: datetime
    next_nvoids_run_at: datetime
    next_live_check_at: datetime
    next_reminder_sweep_at: datetime
    next_resume_tracking_sweep_at: datetime
    next_relationship_sweep_at: datetime
    next_scheduling_sweep_at: datetime

    @classmethod
    def starting_now(cls, owner_id: str) -> "_Schedule":
        now = datetime.now(UTC)
        # The live-reply check calls Gmail inline, so every tenant starting it
        # in the same tick would send N requests a minute at the same instant
        # into a per-minute quota that already returns 429s for one account.
        # A stable per-owner offset spreads them across the interval without
        # any coordination.
        offset = timedelta(
            seconds=int(sha256(owner_id.encode()).hexdigest(), 16) % LIVE_REPLY_CHECK_INTERVAL_SECONDS
        )
        return cls(
            next_run_at=now,
            next_nvoids_run_at=now,
            next_live_check_at=now + offset,
            next_reminder_sweep_at=now,
            next_resume_tracking_sweep_at=now,
            next_relationship_sweep_at=now,
            next_scheduling_sweep_at=now,
        )


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
        # Same reasoning as the relationship sweep above: a default no-op keeps
        # every existing construction site working, and the feature flag gates
        # the sweep either way.
        run_scheduling_sweep: Callable[[Session], None] = lambda _db: None,
        action_lock: Lock,
        stop_event: Event,
        # Which tenants to service this tick. Injected rather than queried
        # here so this service keeps knowing nothing about the User model, and
        # so a test can drive several tenants without a users table.
        list_owners: Callable[[], list[str]] = lambda: [settings.owner_id],
        # Whether this process should be the one doing the work. Injected so
        # the loop's own behaviour can be tested without Redis, and defaulted
        # so every existing construction site keeps working.
        is_leader: Callable[[], bool] = lambda: True,
    ) -> None:
        self._session_factory = session_factory
        self._get_settings = get_settings
        self._run_once = run_once
        self._run_nvoids_once = run_nvoids_once
        self._check_live_replies = check_live_replies
        self._run_reminder_sweep = run_reminder_sweep
        self._run_resume_tracking_sweep = run_resume_tracking_sweep
        self._run_relationship_sweep = run_relationship_sweep
        self._run_scheduling_sweep = run_scheduling_sweep
        self._action_lock = action_lock
        self._stop_event = stop_event
        self._list_owners = list_owners
        self._is_leader = is_leader

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
    def scheduling_sweep_interval_minutes(user_settings: UserSettings) -> int:
        # A 5-minute floor, not the 30 its neighbours use. A 30-minute floor
        # would make a reminder set for 09:15 arrive as late as 09:45, which
        # reads as broken. The sweep is a handful of indexed queries over a
        # small table, so the cost of the tighter cadence is negligible.
        return max(5, min(int(user_settings.feature_scheduling_sweep_interval_minutes or 15), 1440))

    @staticmethod
    def relationship_sweep_interval_minutes() -> int:
        # Clamped exactly as its neighbours are. Read from config rather than
        # UserSettings: the feature's master switch is env-only, so a
        # runtime-tunable cadence for it would buy nothing.
        return max(30, min(int(settings.feature_relationship_sweep_interval_minutes or 720), 1440))

    def run_loop(self) -> None:
        schedules: dict[str, _Schedule] = {}
        was_leader = False
        while not self._stop_event.wait(5):
            # Claimed or renewed every tick, so a process that starts second
            # does nothing and a process whose leader died takes over on its
            # own. Deciding this once at start-up would leave the work stopped
            # until somebody restarted the survivor.
            if not self._is_leader():
                if was_leader:
                    logger.info("Auto runner lost leadership; standing by")
                    was_leader = False
                continue
            if not was_leader:
                logger.info("Auto runner is the leader for this deployment")
                was_leader = True
            try:
                owners = list(self._list_owners())
            except Exception:
                logger.exception("Auto runner could not list owners")
                continue
            # Tenants that went away stop being scheduled, so this cannot grow
            # without bound on a long-running process.
            for gone in set(schedules) - set(owners):
                schedules.pop(gone, None)
            for owner_id in owners:
                schedule = schedules.setdefault(owner_id, _Schedule.starting_now(owner_id))
                try:
                    # Everything below resolves the tenant through
                    # `tenancy.owner_id()`. Without this scope the thread has
                    # no request context, so every tenant's automation ran as
                    # the configured fallback owner - which meant only that one
                    # account was ever serviced.
                    with tenancy.owner_scope(owner_id):
                        self._run_owner_tick(owner_id, schedule)
                except Exception:
                    # One tenant's failure must not stop the other ninety-nine.
                    logger.exception("Auto runner tick failed owner=%s", owner_id)

    def _run_owner_tick(self, owner_id: str, schedule: _Schedule) -> None:
        db = self._session_factory()
        try:
            user_settings = self._get_settings(db)
            if not user_settings.enabled or not user_settings.feature_auto_polling:
                schedule.next_run_at = datetime.now(UTC)
            now_utc = datetime.now(UTC)

            # Runs on its own cadence, outside action_lock, so a live count is
            # visible even while a full sync is in progress under that lock.
            if user_settings.enabled and now_utc >= schedule.next_live_check_at:
                try:
                    self._check_live_replies(db)
                except Exception:
                    logger.exception("Live reply check crashed")
                schedule.next_live_check_at = datetime.now(UTC) + timedelta(seconds=LIVE_REPLY_CHECK_INTERVAL_SECONDS)

            if user_settings.enabled and user_settings.feature_auto_polling and now_utc >= schedule.next_run_at:
                interval_minutes = self.poll_interval_minutes(user_settings)
                with self._action_lock:
                    try:
                        result = self._run_once(None, db)
                        logger.info(
                            "Auto runner enqueued: owner=%s status=%s job_id=%s run_key=%s",
                            owner_id,
                            getattr(result, "status", None),
                            getattr(result, "job_id", None),
                            getattr(result, "run_key", None),
                        )
                    except HTTPException as exc:
                        logger.warning("Auto runner skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                    except Exception:
                        logger.exception("Auto runner crashed during run-once")
                schedule.next_run_at = datetime.now(UTC) + timedelta(minutes=interval_minutes)

            if user_settings.enabled and user_settings.feature_nvoids_enabled and user_settings.feature_nvoids_auto_sync and now_utc >= schedule.next_nvoids_run_at:
                nvoids_interval_minutes = self.nvoids_poll_interval_minutes(user_settings)
                nvoids_limit = self.nvoids_batch_limit(user_settings)
                with self._action_lock:
                    try:
                        result = self._run_nvoids_once(db, nvoids_limit)
                        logger.info(
                            "Auto nvoids sync enqueued: owner=%s status=%s job_id=%s run_key=%s",
                            owner_id,
                            getattr(result, "status", None),
                            getattr(result, "job_id", None),
                            getattr(result, "run_key", None),
                        )
                    except HTTPException as exc:
                        logger.warning("Auto nvoids sync skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                    except Exception:
                        logger.exception("Auto nvoids sync crashed")
                schedule.next_nvoids_run_at = datetime.now(UTC) + timedelta(minutes=nvoids_interval_minutes)

            if (
                user_settings.enabled
                and user_settings.feature_applications_enabled
                and user_settings.feature_application_automation_enabled
                and now_utc >= schedule.next_reminder_sweep_at
            ):
                reminder_interval_minutes = self.reminder_sweep_interval_minutes(user_settings)
                with self._action_lock:
                    try:
                        self._run_reminder_sweep(db)
                    except Exception:
                        logger.exception("Reminder sweep crashed")
                schedule.next_reminder_sweep_at = datetime.now(UTC) + timedelta(minutes=reminder_interval_minutes)

            if (
                user_settings.enabled
                and user_settings.feature_resume_tracking_enabled
                and now_utc >= schedule.next_resume_tracking_sweep_at
            ):
                resume_tracking_interval_minutes = self.resume_tracking_sweep_interval_minutes(user_settings)
                with self._action_lock:
                    try:
                        self._run_resume_tracking_sweep(db)
                    except Exception:
                        logger.exception("Resume tracking sweep crashed")
                schedule.next_resume_tracking_sweep_at = datetime.now(UTC) + timedelta(
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
                and now_utc >= schedule.next_relationship_sweep_at
            ):
                relationship_interval_minutes = self.relationship_sweep_interval_minutes()
                with self._action_lock:
                    try:
                        self._run_relationship_sweep(db)
                    except Exception:
                        logger.exception("Relationship sweep crashed")
                schedule.next_relationship_sweep_at = datetime.now(UTC) + timedelta(
                    minutes=relationship_interval_minutes
                )

            # Expire, warn, enqueue, suspend. Runs in the API process; the
            # work it enqueues runs in the worker.
            if (
                user_settings.enabled
                and settings.feature_scheduling_enabled
                and now_utc >= schedule.next_scheduling_sweep_at
            ):
                scheduling_interval_minutes = self.scheduling_sweep_interval_minutes(user_settings)
                with self._action_lock:
                    try:
                        self._run_scheduling_sweep(db)
                    except Exception:
                        logger.exception("Scheduling sweep crashed")
                schedule.next_scheduling_sweep_at = datetime.now(UTC) + timedelta(
                    minutes=scheduling_interval_minutes
                )
        finally:
            db.close()
