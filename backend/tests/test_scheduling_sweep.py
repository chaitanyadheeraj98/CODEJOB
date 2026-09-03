"""The sweep: expire, warn, enqueue, suspend.

Phase order is load-bearing and is asserted, not assumed. Expiry runs first so a
run that expired this tick is never also warned or enqueued in the same tick.
"""

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    MAX_CONSECUTIVE_FAILURES,
    ScheduledTask,
    ScheduledTaskRun,
)
from app.services.scheduling.sweep import run_scheduling_sweep

OWNER = "owner"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class SweepTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.enqueued: list[int] = []
        self.notifications: list[tuple[str, str, str]] = []

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def enqueue(self, task_id: int) -> None:
        self.enqueued.append(task_id)

    def notify(self, title: str, body: str, kind: str) -> None:
        self.notifications.append((title, body, kind))

    def sweep(self, db: Session, *, now: datetime = NOW, enqueue=None):
        return run_scheduling_sweep(
            db,
            owner_id=OWNER,
            now=now,
            enqueue=enqueue or self.enqueue,
            notify=self.notify,
        )

    def task(self, db: Session, **overrides) -> ScheduledTask:
        values = {
            "owner_id": OWNER,
            "title": "Weekday digest",
            "kind": "digest",
            "schedule_kind": "recurring",
            "cron_expression": "0 9 * * 1-5",
            "timezone": "UTC",
            "status": "active",
            "next_run_at": NOW - timedelta(minutes=1),
        }
        values.update(overrides)
        row = ScheduledTask(**values)
        db.add(row)
        db.flush()
        return row

    def run_row(self, db: Session, task: ScheduledTask, **overrides) -> ScheduledTaskRun:
        values = {
            "owner_id": OWNER,
            "task_id": task.id,
            "started_at": NOW - timedelta(hours=10),
            "outcome": "pending",
            "item_count": 3,
        }
        values.update(overrides)
        row = ScheduledTaskRun(**values)
        db.add(row)
        db.flush()
        return row


class EnqueueTests(SweepTests):
    def test_a_due_task_enqueues_once_and_advances(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db)
            db.commit()
            task_id = task.id

            result = self.sweep(db)

            self.assertEqual(result.due_enqueued, 1)
            self.assertEqual(self.enqueued, [task_id])
            refreshed = db.get(ScheduledTask, task_id)
            self.assertGreater(refreshed.next_run_at, NOW)

    # next_run_at is recomputed at enqueue, not on completion, so a slow job
    # still running on the next tick cannot cause a double fire.
    def test_a_second_tick_does_not_double_enqueue(self) -> None:
        with Session(self.engine) as db:
            self.task(db)
            db.commit()

            self.sweep(db)
            self.sweep(db, now=NOW + timedelta(minutes=5))

            self.assertEqual(len(self.enqueued), 1)

    def test_a_paused_task_is_never_enqueued(self) -> None:
        with Session(self.engine) as db:
            self.task(db, status="paused")
            db.commit()

            result = self.sweep(db)

            self.assertEqual(result.due_enqueued, 0)
            self.assertEqual(self.enqueued, [])

    def test_a_task_with_no_next_run_is_skipped(self) -> None:
        with Session(self.engine) as db:
            self.task(db, kind="checklist", schedule_kind="none", next_run_at=None)
            db.commit()

            self.assertEqual(self.sweep(db).due_enqueued, 0)

    def test_a_one_time_task_does_not_repeat(self) -> None:
        with Session(self.engine) as db:
            task = self.task(
                db,
                schedule_kind="once",
                cron_expression="",
                run_at=NOW - timedelta(minutes=1),
            )
            db.commit()
            task_id = task.id

            self.sweep(db)

            self.assertIsNone(db.get(ScheduledTask, task_id).next_run_at)

    # One task raising must not stop the sweep processing the rest.
    def test_one_failing_enqueue_does_not_stop_the_others(self) -> None:
        with Session(self.engine) as db:
            first = self.task(db, title="Explodes")
            second = self.task(db, title="Fine")
            db.commit()
            bad_id = first.id
            good_id = second.id

            def flaky(task_id: int) -> None:
                if task_id == bad_id:
                    raise RuntimeError("redis is down")
                self.enqueued.append(task_id)

            result = self.sweep(db, enqueue=flaky)

            self.assertEqual(result.errors, 1)
            self.assertEqual(result.due_enqueued, 1)
            self.assertEqual(self.enqueued, [good_id])
            self.assertEqual(db.get(ScheduledTask, bad_id).consecutive_failures, 1)

    def test_the_sweep_is_owner_scoped(self) -> None:
        with Session(self.engine) as db:
            self.task(db, owner_id="somebody-else")
            db.commit()

            self.assertEqual(self.sweep(db).due_enqueued, 0)


class ExpiryTests(SweepTests):
    def test_a_pending_run_past_its_window_expires(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            run = self.run_row(db, task, expires_at=NOW - timedelta(hours=1))
            db.commit()
            run_id = run.id

            result = self.sweep(db)

            self.assertEqual(result.runs_expired, 1)
            expired = db.get(ScheduledTaskRun, run_id)
            self.assertEqual(expired.outcome, "expired")
            self.assertEqual(expired.expiry_reason, "not_reviewed")
            self.assertEqual(expired.expired_at, NOW)

    def test_a_run_inside_its_window_is_untouched(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            run = self.run_row(db, task, expires_at=NOW + timedelta(hours=1))
            db.commit()
            run_id = run.id

            self.assertEqual(self.sweep(db).runs_expired, 0)
            self.assertEqual(db.get(ScheduledTaskRun, run_id).outcome, "pending")

    def test_a_run_with_no_expiry_never_expires(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            self.run_row(db, task, outcome="notified", expires_at=None)
            db.commit()

            self.assertEqual(self.sweep(db).runs_expired, 0)


class WarningTests(SweepTests):
    def test_the_half_window_warning_fires_once(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            # Started 10h ago, expires in 2h: well past halfway.
            self.run_row(db, task, expires_at=NOW + timedelta(hours=2))
            db.commit()

            first = self.sweep(db)
            second = self.sweep(db, now=NOW + timedelta(minutes=30))

            self.assertEqual(first.runs_warned, 1)
            self.assertEqual(second.runs_warned, 0)
            self.assertEqual(len(self.notifications), 1)

    def test_no_warning_before_halfway(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            self.run_row(
                db,
                task,
                started_at=NOW - timedelta(hours=1),
                expires_at=NOW + timedelta(hours=10),
            )
            db.commit()

            self.assertEqual(self.sweep(db).runs_warned, 0)

    # A warning after expiry warns about something that already happened.
    def test_no_warning_fires_after_expiry(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db, next_run_at=None)
            self.run_row(db, task, expires_at=NOW - timedelta(hours=1))
            db.commit()

            result = self.sweep(db)

            self.assertEqual(result.runs_expired, 1)
            self.assertEqual(result.runs_warned, 0)

    # Phase order, asserted rather than assumed.
    def test_expiry_runs_before_enqueue_in_one_pass(self) -> None:
        with Session(self.engine) as db:
            task = self.task(db)
            self.run_row(db, task, expires_at=NOW - timedelta(hours=1))
            db.commit()
            run_id = db.query(ScheduledTaskRun).one().id

            seen: list[str] = []

            def record(task_id: int) -> None:
                seen.append(db.get(ScheduledTaskRun, run_id).outcome)

            self.sweep(db, enqueue=record)

            # By the time the enqueue phase runs, expiry is already written.
            # Reading it from the same session is the whole assertion: a probe
            # on a second session would only observe commit timing.
            self.assertEqual(seen, ["expired"])
            self.assertEqual(db.get(ScheduledTaskRun, run_id).outcome, "expired")


class SuspensionTests(SweepTests):
    def test_repeated_failures_suspend_the_task(self) -> None:
        with Session(self.engine) as db:
            task = self.task(
                db,
                next_run_at=None,
                consecutive_failures=MAX_CONSECUTIVE_FAILURES,
                last_error="boom",
            )
            db.commit()
            task_id = task.id

            result = self.sweep(db)

            self.assertEqual(result.tasks_suspended, 1)
            suspended = db.get(ScheduledTask, task_id)
            self.assertEqual(suspended.status, "suspended")
            self.assertIsNone(suspended.next_run_at)
            self.assertTrue(any(kind == "failure" for _, _, kind in self.notifications))

    def test_a_task_below_the_threshold_keeps_running(self) -> None:
        with Session(self.engine) as db:
            self.task(db, next_run_at=None, consecutive_failures=MAX_CONSECUTIVE_FAILURES - 1)
            db.commit()

            self.assertEqual(self.sweep(db).tasks_suspended, 0)

    def test_a_suspended_task_is_not_re_suspended(self) -> None:
        with Session(self.engine) as db:
            self.task(
                db,
                next_run_at=None,
                status="suspended",
                consecutive_failures=MAX_CONSECUTIVE_FAILURES + 5,
            )
            db.commit()

            self.assertEqual(self.sweep(db).tasks_suspended, 0)


if __name__ == "__main__":
    unittest.main()
