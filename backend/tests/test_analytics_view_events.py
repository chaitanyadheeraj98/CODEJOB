import os
import sqlite3
import unittest

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ProductivityEvent


class AnalyticsViewEventTests(unittest.TestCase):
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
        self.client = TestClient(main.app)
        self.original_record_productivity_event = main._record_productivity_event

    def tearDown(self) -> None:
        main._record_productivity_event = self.original_record_productivity_event
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_view_event_records_normally(self) -> None:
        response = self.client.post(
            "/analytics/events/view",
            json={
                "event_type": "view_needs_review",
                "event_source": "ui",
                "metadata": {"page": "needs_review", "range": "current_day"},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreater(payload["id"], 0)
        self.assertEqual(payload["event_type"], "view_needs_review")
        self.assertEqual(payload["metadata"]["page"], "needs_review")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ProductivityEvent).count(), 1)

    def test_view_event_drops_sqlite_locked_error(self) -> None:
        def locked_record(*_args, **_kwargs):
            raise OperationalError("INSERT", {}, sqlite3.OperationalError("database is locked"))

        main._record_productivity_event = locked_record

        response = self.client.post(
            "/analytics/events/view",
            json={
                "event_type": "view_needs_review",
                "event_source": "ui",
                "metadata": {"page": "needs_review", "range": "current_day"},
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["id"], 0)
        self.assertEqual(payload["event_type"], "view_needs_review")
        self.assertEqual(payload["event_source"], "ui")
        self.assertEqual(payload["metadata"]["page"], "needs_review")
        with self.SessionLocal() as db:
            self.assertEqual(db.query(ProductivityEvent).count(), 0)

    def test_view_event_keeps_unsupported_event_type_strict(self) -> None:
        response = self.client.post(
            "/analytics/events/view",
            json={"event_type": "approved_sent", "event_source": "ui"},
        )

        self.assertEqual(response.status_code, 400, response.text)


if __name__ == "__main__":
    unittest.main()
