import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from rq.job import JobStatus

from app import main
from app.db import Base
from app.jobs.progress import update_job_progress
from app.jobs.queues import get_queue
from app.models import RecentRun, UserSettings
from app.recent_runs import create_recent_run


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        return SimpleNamespace(id=kwargs["job_id"])


class BackgroundJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        with self.SessionLocal() as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    enabled=True,
                    gmail_query="is:unread",
                    default_gmail_query="is:unread",
                    saved_gmail_queries_json="[]",
                    feature_nvoids_enabled=True,
                    nvoids_batch_limit=7,
                )
            )
            db.commit()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        self.engine.dispose()

    def test_nvoids_enqueue_returns_immediately_and_persists_queue_metadata(self) -> None:
        queue = _FakeQueue()
        with (
            patch.object(main, "active_job_id", return_value=None),
            patch.object(main, "get_queue", return_value=queue),
        ):
            response = self.client.post("/jobs/nvoids-sync?batch_limit=4")

        self.assertEqual(response.status_code, 202)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(len(queue.calls), 1)
        self.assertEqual(queue.calls[0]["kwargs"]["max_items"], 4)
        with self.SessionLocal() as db:
            row = db.query(RecentRun).filter(RecentRun.run_key == payload["run_key"]).one()
            self.assertEqual(row.queue_name, "nvoids_sync")
            self.assertEqual(row.job_backend_id, payload["job_id"])
            self.assertEqual(row.total_items, 4)
            self.assertEqual(row.progress_pct, 0.0)

        status = self.client.get(f"/jobs/{payload['run_key']}")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["processed_items"], 0)

    def test_duplicate_job_is_rejected_before_creating_recent_run(self) -> None:
        with patch.object(main, "active_job_id", return_value="already-running"):
            response = self.client.post("/jobs/nvoids-sync")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "another_job_in_progress")
        self.assertEqual(response.json()["detail"]["job_id"], "already-running")
        self.assertIsNone(response.json()["detail"]["run_key"])

    def test_duplicate_job_conflict_includes_run_key_when_a_recent_run_row_exists(self) -> None:
        with self.SessionLocal() as db:
            create_recent_run(
                db, owner_id=main.settings.owner_id, run_source="nvoids_sync",
                run_key="nvoids_sync:already-running-key", status="running", detail="in progress",
                job_backend_id="already-running",
            )
            db.commit()
        with patch.object(main, "active_job_id", return_value="already-running"):
            response = self.client.post("/jobs/nvoids-sync")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["run_key"], "nvoids_sync:already-running-key")

    def test_jobs_summary_counts_recent_runs_and_survives_redis_outage(self) -> None:
        with self.SessionLocal() as db:
            create_recent_run(
                db, owner_id=main.settings.owner_id, run_source="nvoids_sync",
                run_key="nvoids_sync:summary-ok", status="ok", detail="done",
            )
            create_recent_run(
                db, owner_id=main.settings.owner_id, run_source="nvoids_sync",
                run_key="nvoids_sync:summary-failed", status="failed", detail="boom",
            )
            db.commit()

        with patch.object(main, "get_redis_connection", side_effect=ConnectionError("redis down")):
            response = self.client.get("/jobs/summary")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["succeeded"], 1)
        self.assertEqual(payload["failed"], 1)
        self.assertEqual(payload["queued"], 0)
        self.assertEqual(payload["processing"], 0)

    def test_progress_is_monotonic_and_mirrored_to_rq_metadata(self) -> None:
        job = SimpleNamespace(meta={}, save_meta=lambda: None)
        with self.SessionLocal() as db:
            create_recent_run(
                db,
                owner_id=main.settings.owner_id,
                run_source="nvoids_sync",
                run_key="nvoids_sync:test-progress",
                status="queued",
                detail="queued",
                total_items=4,
                processed_items=0,
                progress_pct=0.0,
            )
            db.commit()
            with patch("app.jobs.progress.get_current_job", return_value=job):
                first = update_job_progress(
                    db,
                    run_key="nvoids_sync:test-progress",
                    processed_items=2,
                    total_items=4,
                    status="running",
                )
                second = update_job_progress(
                    db,
                    run_key="nvoids_sync:test-progress",
                    processed_items=1,
                    total_items=2,
                    complete=True,
                )

        self.assertEqual(first.processed_items, 2)
        self.assertEqual(second.processed_items, 2)
        self.assertEqual(second.total_items, 4)
        self.assertEqual(second.progress_pct, 100.0)
        self.assertEqual(job.meta["processed_items"], 2)

    def test_status_and_cancel_follow_rq_during_post_processing_race(self) -> None:
        with self.SessionLocal() as db:
            create_recent_run(
                db,
                owner_id=main.settings.owner_id,
                run_source="nvoids_sync",
                run_key="nvoids_sync:race",
                status="ok",
                detail="Core sync complete.",
                job_backend_id="race-job",
                total_items=3,
                processed_items=3,
                progress_pct=99.0,
                queue_name="nvoids_sync",
            )
            db.commit()

        job = SimpleNamespace(
            id="race-job",
            connection=SimpleNamespace(),
            get_status=lambda refresh=True: JobStatus.STARTED,
        )
        with (
            patch.object(main.Job, "fetch", return_value=job),
            patch.object(main, "get_redis_connection", return_value=SimpleNamespace()),
        ):
            status = self.client.get("/jobs/nvoids_sync%3Arace")
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["status"], "running")

        with (
            patch.object(main.Job, "fetch", return_value=job),
            patch.object(main, "get_redis_connection", return_value=SimpleNamespace()),
            patch.object(main, "send_stop_job_command") as stop_job,
        ):
            canceled = self.client.post("/jobs/nvoids_sync%3Arace/cancel")
        self.assertEqual(canceled.status_code, 200)
        self.assertEqual(canceled.json()["status"], "canceled")
        stop_job.assert_called_once_with(job.connection, "race-job")

    def test_unknown_queue_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown queue"):
            get_queue("not-a-real-queue", connection=SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
