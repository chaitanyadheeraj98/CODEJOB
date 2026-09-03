import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import RecentRun, RecentRunSkippedItem


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        return SimpleNamespace(id=kwargs["job_id"])


class RetryRecentRunSkippedItemsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_retry_enqueues_a_job_scoped_to_only_owned_message_ids(self) -> None:
        with Session(self.engine) as db:
            owned = RecentRunSkippedItem(
                owner_id=main.settings.owner_id,
                run_source="automation_run",
                run_key="rk-old",
                external_message_id="m-owned",
            )
            other_owner = RecentRunSkippedItem(
                owner_id="someone-else",
                run_source="automation_run",
                run_key="rk-old",
                external_message_id="m-not-owned",
            )
            db.add_all([owned, other_owner])
            db.commit()
            owned_id = owned.id
            other_id = other_owner.id

        queue = _FakeQueue()
        with (
            patch.object(main, "active_job_id", return_value=None),
            patch.object(main, "get_queue", return_value=queue),
        ):
            res = self.client.post(
                "/recent-runs/skipped/retry",
                json={"skipped_item_ids": [owned_id, other_id]},
            )

        self.assertEqual(res.status_code, 202)
        payload = res.json()
        self.assertEqual(payload["status"], "queued")
        self.assertEqual(len(queue.calls), 1)
        self.assertEqual(queue.calls[0]["task"], main.run_retry_selected_messages_job)
        self.assertEqual(queue.calls[0]["kwargs"]["external_message_ids"], ["m-owned"])
        with self.SessionLocal() as db:
            row = db.query(RecentRun).filter(RecentRun.run_key == payload["run_key"]).one()
            self.assertEqual(row.queue_name, "automation_run")
            self.assertEqual(row.total_items, 1)

    def test_retry_rejects_empty_selection(self) -> None:
        res = self.client.post("/recent-runs/skipped/retry", json={"skipped_item_ids": []})
        self.assertEqual(res.status_code, 422)

    def test_retry_400s_when_selected_rows_have_no_message_id(self) -> None:
        with Session(self.engine) as db:
            row = RecentRunSkippedItem(
                owner_id=main.settings.owner_id,
                run_source="nvoids_sync",
                run_key="rk-old",
                external_message_id=None,
            )
            db.add(row)
            db.commit()
            row_id = row.id

        res = self.client.post("/recent-runs/skipped/retry", json={"skipped_item_ids": [row_id]})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
