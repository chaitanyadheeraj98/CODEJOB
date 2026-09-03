"""The four handlers, and the boundary that makes draft-and-hold true.

The load-bearing test here is `test_a_run_writes_only_the_two_scheduling_tables`.
"Draft-and-hold" is a property of code, and the cheapest way to keep it true
through later edits is a test that fails the moment any other table changes
during a run.
"""

import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine, func, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import (
    Application,
    ChatMessage,
    ChatSession,
    ScheduledTask,
    ScheduledTaskRun,
    utc_now,
)

OWNER = "owner"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


class HandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.settings_patch = patch("app.config.settings.owner_id", OWNER)
        self.settings_patch.start()

    def tearDown(self) -> None:
        self.settings_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def task(self, db: Session, **overrides) -> ScheduledTask:
        values = {
            "owner_id": OWNER,
            "title": "Morning check",
            "kind": "reminder",
            "schedule_kind": "once",
            "timezone": "UTC",
            "status": "active",
            "action_json": "{}",
        }
        values.update(overrides)
        row = ScheduledTask(**values)
        db.add(row)
        db.flush()
        return row

    def run_for(self, db: Session, task: ScheduledTask) -> ScheduledTaskRun:
        row = ScheduledTaskRun(
            owner_id=task.owner_id, task_id=task.id, started_at=NOW, outcome="pending"
        )
        db.add(row)
        db.flush()
        return row

    def application(self, db: Session, application_id: int, *, status_changed_at: datetime) -> Application:
        row = Application(
            id=application_id,
            owner_id=OWNER,
            resume_asset_id=application_id,
            resume_version_snapshot=1,
            resume_file_name_snapshot=f"r{application_id}.pdf",
            resume_sha256_snapshot=str(application_id % 10) * 64,
            recruiter_opportunity_id=application_id,
            recruiter_contact_id=1,
            job_title_snapshot="Java Developer",
            end_client_snapshot="Bank X",
            status="contacted",
            status_changed_at=status_changed_at,
            created_at=status_changed_at,
            updated_at=status_changed_at,
        )
        db.add(row)
        db.flush()
        return row


class ReminderTests(HandlerTests):
    def test_a_reminder_notifies_and_finishes_notified(self) -> None:
        from app.services.scheduling.handlers.reminder import run_reminder

        with self.SessionLocal() as db:
            task = self.task(db, action_json=json.dumps({"note": "Call the recruiter back"}))
            run = self.run_for(db, task)
            db.commit()

            run_reminder(db, task, run)
            db.commit()

            self.assertEqual(run.outcome, "notified")
            self.assertEqual(run.item_count, 0)
            # A reminder has nothing to approve, so it must not carry an expiry
            # that would later mark it "expired" - which reads as a failure.
            self.assertIsNone(run.expires_at)
            self.assertIsNotNone(run.finished_at)

    def test_the_notification_lands_in_the_dedicated_session(self) -> None:
        from app.services.scheduling.handlers.reminder import run_reminder

        with self.SessionLocal() as db:
            # An unrelated, more recently updated conversation. The notification
            # must not land here: chat_history_max_messages is 20, and a daily
            # digest would evict a fifth of the model's usable history.
            db.add(ChatSession(owner_id=OWNER, title="Real conversation", updated_at=utc_now()))
            task = self.task(db)
            run = self.run_for(db, task)
            db.commit()

            run_reminder(db, task, run)
            db.commit()

            message = db.query(ChatMessage).one()
            session = db.get(ChatSession, message.session_id)
            self.assertEqual(session.title, "Notifications")


class DigestTests(HandlerTests):
    def test_an_empty_digest_says_so_explicitly(self) -> None:
        from app.services.scheduling.handlers.digest import run_digest

        with self.SessionLocal() as db:
            task = self.task(db, kind="digest", title="Daily digest")
            run = self.run_for(db, task)
            db.commit()

            run_digest(db, task, run)
            db.commit()

            body = db.query(ChatMessage).one().content
            # Empty cards are indistinguishable from a digest that failed.
            self.assertIn("Nothing is pending", body)
            self.assertEqual(run.item_count, 0)

    def test_a_digest_counts_pending_work_and_carries_provenance(self) -> None:
        from app.services.scheduling.handlers.digest import run_digest

        with self.SessionLocal() as db:
            task = self.task(db, kind="digest", title="Daily digest")
            run = self.run_for(db, task)
            other = self.task(db, kind="workflow", title="Follow-ups")
            db.add(
                ScheduledTaskRun(
                    owner_id=OWNER, task_id=other.id, started_at=NOW, outcome="pending", item_count=4
                )
            )
            db.commit()

            run_digest(db, task, run)
            db.commit()

            payload = json.loads(run.prepared_json)
            self.assertEqual(payload["total"], 1)
            provenance = payload["provenance"]
            self.assertEqual(provenance["metric"], "Pending work")
            self.assertTrue(provenance["assumptions"])


class MonitorTests(HandlerTests):
    def test_a_monitor_that_fires_names_the_records(self) -> None:
        from app.services.scheduling.handlers.monitor import run_monitor

        with self.SessionLocal() as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=40))
            task = self.task(
                db,
                kind="monitor",
                title="Stale applications",
                condition_json=json.dumps(
                    {"predicate": "status_unchanged_for_days", "subject_type": "application", "days": 21}
                ),
            )
            run = self.run_for(db, task)
            db.commit()

            run_monitor(db, task, run)
            db.commit()

            payload = json.loads(run.prepared_json)
            self.assertTrue(payload["fired"])
            self.assertEqual(payload["subject_ids"], ["1"])
            self.assertTrue(payload["evidence"])
            self.assertEqual(run.item_count, 1)

    # A quiet check is a check that worked. Leaving it pending would make it
    # expire later and read as a failure in run history.
    def test_a_monitor_that_does_not_fire_still_completes(self) -> None:
        from app.services.scheduling.handlers.monitor import run_monitor

        with self.SessionLocal() as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=1))
            task = self.task(
                db,
                kind="monitor",
                condition_json=json.dumps(
                    {"predicate": "status_unchanged_for_days", "subject_type": "application", "days": 21}
                ),
            )
            run = self.run_for(db, task)
            db.commit()

            run_monitor(db, task, run)
            db.commit()

            self.assertEqual(run.outcome, "notified")
            self.assertEqual(run.item_count, 0)
            self.assertEqual(db.query(ChatMessage).count(), 0)

    def test_an_unreadable_condition_raises_rather_than_doing_nothing(self) -> None:
        from app.services.scheduling.conditions import UnknownPredicate
        from app.services.scheduling.handlers.monitor import run_monitor

        with self.SessionLocal() as db:
            task = self.task(db, kind="monitor", condition_json="not json")
            run = self.run_for(db, task)
            db.commit()

            with self.assertRaises(UnknownPredicate):
                run_monitor(db, task, run)


class WorkflowTests(HandlerTests):
    def condition(self) -> str:
        return json.dumps(
            {"predicate": "status_unchanged_for_days", "subject_type": "application", "days": 21}
        )

    def test_a_workflow_prepares_items_and_holds_them(self) -> None:
        from app.services.scheduling.handlers.workflow import run_workflow

        with self.SessionLocal() as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=40))
            task = self.task(
                db, kind="workflow", title="Chase stale", condition_json=self.condition()
            )
            run = self.run_for(db, task)
            db.commit()

            run_workflow(db, task, run)
            db.commit()

            self.assertEqual(run.outcome, "pending")
            self.assertEqual(run.item_count, 1)
            self.assertIsNotNone(run.expires_at)
            item = json.loads(run.prepared_json)["items"][0]
            # Shaped for ApplicationPatchRequest, the route that has validated
            # this since v2. Nothing new validates a record change.
            self.assertEqual(set(item["payload"]), {"next_action_type", "next_action_at"})
            self.assertEqual(item["record_kind"], "application")

    def test_a_workflow_with_nothing_to_prepare_does_not_sit_pending(self) -> None:
        from app.services.scheduling.handlers.workflow import run_workflow

        with self.SessionLocal() as db:
            task = self.task(db, kind="workflow", condition_json=self.condition())
            run = self.run_for(db, task)
            db.commit()

            run_workflow(db, task, run)
            db.commit()

            self.assertEqual(run.outcome, "notified")
            self.assertIsNone(run.expires_at)

    def test_preparing_changes_no_application(self) -> None:
        from app.services.scheduling.handlers.workflow import run_workflow

        with self.SessionLocal() as db:
            application = self.application(db, 1, status_changed_at=NOW - timedelta(days=40))
            task = self.task(db, kind="workflow", condition_json=self.condition())
            run = self.run_for(db, task)
            db.commit()
            before = (application.next_action_at, application.next_action_type, application.updated_at)

            run_workflow(db, task, run)
            db.commit()

            refreshed = db.get(Application, 1)
            self.assertEqual(
                (refreshed.next_action_at, refreshed.next_action_type, refreshed.updated_at), before
            )


class JobBoundaryTests(HandlerTests):
    """The draft-and-hold boundary, asserted by snapshot."""

    ALLOWED = {"scheduled_tasks", "scheduled_task_runs", "chat_sessions", "chat_messages"}

    def counts(self, db: Session) -> dict[str, int]:
        return {
            name: db.execute(
                func.count().select().select_from(Base.metadata.tables[name])
            ).scalar_one()
            for name in inspect(self.engine).get_table_names()
            if name in Base.metadata.tables
        }

    def test_a_run_writes_only_the_two_scheduling_tables(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            self.application(db, 1, status_changed_at=NOW - timedelta(days=40))
            task = self.task(
                db,
                kind="workflow",
                condition_json=json.dumps(
                    {"predicate": "status_unchanged_for_days", "subject_type": "application", "days": 21}
                ),
            )
            db.commit()
            task_id = task.id
            before = self.counts(db)

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            result = run_scheduled_task_job(task_id=task_id)

        with self.SessionLocal() as db:
            after = self.counts(db)

        self.assertEqual(result["status"], "ok")
        changed = {name for name in before if before[name] != after[name]}
        # chat_* are the notification channel, which is a write but not a
        # record change; everything else must be untouched.
        self.assertFalse(changed - self.ALLOWED, f"unexpected writes: {changed - self.ALLOWED}")
        self.assertIn("scheduled_task_runs", changed)

    def test_a_failing_handler_records_the_failure_and_counts_it(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            task = self.task(db, kind="monitor", condition_json="not json")
            db.commit()
            task_id = task.id

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            result = run_scheduled_task_job(task_id=task_id)

        with self.SessionLocal() as db:
            refreshed = db.get(ScheduledTask, task_id)
            run = db.query(ScheduledTaskRun).one()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(refreshed.consecutive_failures, 1)
        self.assertIsNotNone(refreshed.last_error)
        self.assertEqual(run.outcome, "failed")

    def test_a_successful_run_resets_the_failure_counter(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            task = self.task(db, consecutive_failures=2)
            db.commit()
            task_id = task.id

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            run_scheduled_task_job(task_id=task_id)

        with self.SessionLocal() as db:
            self.assertEqual(db.get(ScheduledTask, task_id).consecutive_failures, 0)

    def test_a_paused_task_is_skipped_by_the_job_too(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            task = self.task(db, status="paused")
            db.commit()
            task_id = task.id

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            result = run_scheduled_task_job(task_id=task_id)

        self.assertEqual(result["status"], "skipped")

    def test_a_newer_digest_run_supersedes_an_older_pending_one(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            task = self.task(db, kind="digest", title="Daily digest")
            db.add(
                ScheduledTaskRun(
                    owner_id=OWNER,
                    task_id=task.id,
                    started_at=NOW - timedelta(days=1),
                    outcome="pending",
                    item_count=2,
                )
            )
            db.commit()
            task_id = task.id

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            run_scheduled_task_job(task_id=task_id)

        with self.SessionLocal() as db:
            older = db.query(ScheduledTaskRun).order_by(ScheduledTaskRun.id.asc()).first()
            self.assertEqual(older.outcome, "expired")
            self.assertEqual(older.expiry_reason, "superseded")

    def test_a_checklist_is_never_enqueued(self) -> None:
        from app.jobs.tasks import run_scheduled_task_job

        with self.SessionLocal() as db:
            task = self.task(db, kind="checklist", schedule_kind="none")
            db.commit()
            task_id = task.id

        with patch("app.jobs.tasks.SessionLocal", self.SessionLocal):
            result = run_scheduled_task_job(task_id=task_id)

        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
