"""One sweep pass: expire, warn, enqueue, suspend. In that order, deliberately.

Expiry runs FIRST so a run that expired this tick is never also enqueued for an
approval notification in the same tick, and so a superseding run never races the
batch it supersedes.

Expiry ships here rather than in a later work item because this repo already
carries two `expires_at` columns that are written, displayed, and never compared
against a clock. The moment runs exist, unreviewed batches start ageing; a sweep
that creates work without expiring it reproduces that defect by construction.

A plain function taking a Session. Startup is skipped under pytest, so a sweep
reachable only through the background thread would be untestable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import (
    MAX_CONSECUTIVE_FAILURES,
    ScheduledTask,
    ScheduledTaskRun,
    utc_now,
)
from app.services.scheduling.schedule import ScheduleSpec, next_run_after

logger = logging.getLogger(__name__)

EXPIRY_NOT_REVIEWED = "not_reviewed"
EXPIRY_SUPERSEDED = "superseded"


@dataclass(frozen=True)
class SweepResult:
    due_enqueued: int = 0
    runs_expired: int = 0
    runs_warned: int = 0
    tasks_suspended: int = 0
    errors: int = 0


def _spec(task: ScheduledTask) -> ScheduleSpec:
    return ScheduleSpec(
        schedule_kind=task.schedule_kind,
        cron_expression=task.cron_expression or "",
        run_at=task.run_at,
        timezone=task.timezone or "UTC",
    )


def _default_enqueue(task_id: int) -> None:
    """Hand the run to the worker. Imported lazily so tests need no Redis."""
    from app.jobs.queues import SCHEDULED_TASK_QUEUE, get_queue
    from app.jobs.tasks import run_scheduled_task_job

    get_queue(SCHEDULED_TASK_QUEUE).enqueue(run_scheduled_task_job, task_id=task_id)


def expire_due_runs(db: Session, *, owner_id: str, now: datetime) -> int:
    """Mark pending runs past their window expired. The F7 check, enforced."""
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.owner_id == owner_id,
            ScheduledTaskRun.outcome == "pending",
            ScheduledTaskRun.expires_at.isnot(None),
            ScheduledTaskRun.expires_at <= now,
        )
        .all()
    )
    for row in rows:
        row.outcome = "expired"
        row.expired_at = now
        row.expiry_reason = EXPIRY_NOT_REVIEWED
    return len(rows)


def warn_half_window(
    db: Session,
    *,
    owner_id: str,
    now: datetime,
    notify: Callable[[str, str, str], None],
) -> int:
    """Warn once, past the halfway point, and never after expiry.

    `warned_at` is what makes it once. Without a stamp every sweep past the
    halfway point re-warns, which trains the user to ignore the warning.
    """
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.owner_id == owner_id,
            ScheduledTaskRun.outcome == "pending",
            ScheduledTaskRun.warned_at.is_(None),
            ScheduledTaskRun.expires_at.isnot(None),
        )
        .all()
    )
    warned = 0
    for row in rows:
        if row.expires_at <= now:
            # Expiry already handled this one in phase 1; a warning now would
            # arrive after the thing it warns about.
            continue
        started = row.started_at or now
        halfway = started + (row.expires_at - started) / 2
        if now < halfway:
            continue
        row.warned_at = now
        task = db.get(ScheduledTask, row.task_id)
        title = task.title if task else "Scheduled work"
        notify(
            f"{title}: review closes soon",
            f"{row.item_count} prepared item(s) expire at {row.expires_at:%Y-%m-%d %H:%M} UTC.",
            "warning",
        )
        warned += 1
    return warned


def enqueue_due_tasks(
    db: Session,
    *,
    owner_id: str,
    now: datetime,
    enqueue: Callable[[int], None],
) -> tuple[int, int]:
    """Enqueue every active task whose next run has arrived.

    `next_run_at` is recomputed at enqueue time, not on completion, so a slow or
    failing job cannot cause a double-fire on the next tick.
    """
    tasks = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.owner_id == owner_id,
            ScheduledTask.status == "active",
            ScheduledTask.next_run_at.isnot(None),
            ScheduledTask.next_run_at <= now,
        )
        .all()
    )
    enqueued = 0
    errors = 0
    for task in tasks:
        # Recompute first. If the enqueue raises, the task still moves on rather
        # than being retried every five minutes forever.
        task.next_run_at = next_run_after(_spec(task), now)
        task.last_run_at = now
        try:
            enqueue(task.id)
            enqueued += 1
        except Exception:
            logger.exception("scheduling_enqueue_failed task_id=%s", task.id)
            task.last_error = "Could not enqueue the run."
            task.consecutive_failures = (task.consecutive_failures or 0) + 1
            errors += 1
    return enqueued, errors


def suspend_failing_tasks(
    db: Session,
    *,
    owner_id: str,
    notify: Callable[[str, str, str], None],
) -> int:
    """Repeated failure suspends rather than retrying forever.

    RQ's Retry is used elsewhere in this codebase but not here: a scheduled task
    that fails should surface, and the next tick is the natural retry.
    """
    tasks = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.owner_id == owner_id,
            ScheduledTask.status == "active",
            ScheduledTask.consecutive_failures >= MAX_CONSECUTIVE_FAILURES,
        )
        .all()
    )
    for task in tasks:
        task.status = "suspended"
        task.next_run_at = None
        notify(
            f"{task.title}: paused after repeated failures",
            f"{task.consecutive_failures} consecutive failures. Last error: "
            f"{(task.last_error or 'unknown')[:200]}",
            "failure",
        )
    return len(tasks)


def run_scheduling_sweep(
    db: Session,
    *,
    owner_id: str,
    now: datetime | None = None,
    enqueue: Callable[[int], None] | None = None,
    notify: Callable[[str, str, str], None] | None = None,
) -> SweepResult:
    """One pass. Both side-effecting collaborators are injectable so this is
    testable without Redis and without a chat session."""
    moment = now or utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    send = notify or _default_notify
    hand_off = enqueue or _default_enqueue

    expired = expire_due_runs(db, owner_id=owner_id, now=moment)
    warned = warn_half_window(db, owner_id=owner_id, now=moment, notify=send)
    enqueued, errors = enqueue_due_tasks(db, owner_id=owner_id, now=moment, enqueue=hand_off)
    suspended = suspend_failing_tasks(db, owner_id=owner_id, notify=send)
    db.commit()

    return SweepResult(
        due_enqueued=enqueued,
        runs_expired=expired,
        runs_warned=warned,
        tasks_suspended=suspended,
        errors=errors,
    )


def _default_notify(title: str, body: str, kind: str) -> None:
    from app.db import SessionLocal
    from app.services import proactive_notification_service

    with SessionLocal() as db:
        proactive_notification_service.notify_scheduled(db, title=title, body=body, kind=kind)
