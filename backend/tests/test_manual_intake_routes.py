"""The two manual-intake routes: duplicate preview, and queued creation.

Creation returns a run key rather than a card, because extraction, scoring and
drafting are several model calls and the user should not be holding an open
request through them. The client polls GET /jobs/{run_key} - the same endpoint
the Gmail and Nvoids syncs already report through.

The test that matters most here is the one asserting a paste still enqueues
while a Gmail sync is running. Manual intake has its own queue precisely because
`_enqueue_background_job` returns 409 when the target queue is busy, and a sync
running is exactly when someone is most likely to be pasting.
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.jobs.queues import GMAIL_SYNC_QUEUE, MANUAL_INTAKE_QUEUE
from app.models import RecentRun, RecruiterEmail, UserSettings
from app.services.manual_intake_service import ManualDuplicate

PASTE = """Job Title: Senior Full Stack Developer
Job Location: Plano, TX

Thanks & regards
T Mahesh royal
Email: mahesh@fusiongts.com
"""


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        return SimpleNamespace(id=kwargs["job_id"])


class ManualIntakeRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=main.settings.owner_id))
            db.commit()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _create(self, text: str = PASTE, **body):
        queue = _FakeQueue()
        with (
            patch.object(main, "active_job_id", return_value=None),
            patch.object(main, "get_queue", return_value=queue),
        ):
            response = self.client.post("/manual-requirements", json={"text": text, **body})
        return response, queue

    # --- D. queued progress -----------------------------------------------

    def test_creation_queues_and_returns_a_run_key(self) -> None:
        response, queue = self._create()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["run_key"].startswith("manual_intake:"))
        self.assertEqual(len(queue.calls), 1)
        self.assertEqual(queue.calls[0]["kwargs"]["text"], PASTE)

    def test_no_card_is_created_synchronously(self) -> None:
        self._create()
        with self.SessionLocal() as db:
            self.assertEqual(db.query(RecruiterEmail).count(), 0)

    def test_the_run_is_recorded_under_its_own_run_source(self) -> None:
        response, _ = self._create()
        with self.SessionLocal() as db:
            row = db.query(RecentRun).filter_by(run_key=response.json()["run_key"]).one()
        self.assertEqual(row.run_source, "manual_intake")
        self.assertEqual(row.queue_name, MANUAL_INTAKE_QUEUE)
        self.assertEqual(row.total_items, 1)
        self.assertEqual(row.status, "queued")

    def test_a_paste_still_enqueues_while_a_gmail_sync_is_running(self) -> None:
        """The regression test for the dedicated queue.

        On a shared queue this returns 409, and the user cannot paste whenever a
        sync happens to be in flight.
        """
        queue = _FakeQueue()
        busy = {GMAIL_SYNC_QUEUE: "job-in-flight"}
        with (
            patch.object(main, "active_job_id", side_effect=lambda name: busy.get(name)),
            patch.object(main, "get_queue", return_value=queue),
        ):
            response = self.client.post("/manual-requirements", json={"text": PASTE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(queue.calls), 1)

    def test_a_second_paste_while_one_is_running_is_refused_clearly(self) -> None:
        with patch.object(main, "active_job_id", return_value="manual-job-1"):
            response = self.client.post("/manual-requirements", json={"text": PASTE})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "another_job_in_progress")

    def test_the_status_endpoint_serves_the_run(self) -> None:
        """No bespoke status route: GET /jobs/{run_key} already does this."""
        response, _ = self._create()
        run_key = response.json()["run_key"]
        status = self.client.get(f"/jobs/{run_key}")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], "queued")

    # --- input bounds -----------------------------------------------------

    def test_empty_and_whitespace_text_are_refused_before_any_work(self) -> None:
        self.assertEqual(
            self.client.post("/manual-requirements", json={"text": ""}).status_code, 422
        )
        response, queue = self._create(text="   \n  ")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(queue.calls, [])

    def test_an_oversized_paste_is_refused_at_the_boundary(self) -> None:
        """Refused before a job is enqueued and before any model call."""
        response, queue = self._create(text="x" * (main.settings.manual_intake_max_chars + 1))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(queue.calls, [])

    def test_the_refusal_says_how_long_the_paste_is_and_how_long_it_may_be(self) -> None:
        """The client renders `detail` when it is a string, and nothing useful
        otherwise. Pydantic's 422 detail is a list of error objects, so an
        over-length paste used to surface as "Could not queue the requirement",
        which names neither number the user needs."""
        response, _ = self._create(text="x" * 20_050)
        detail = response.json()["detail"]
        self.assertIsInstance(detail, str)
        self.assertIn("20,050", detail)
        self.assertIn(f"{main.settings.manual_intake_max_chars:,}", detail)

    def test_the_cap_is_the_configured_one(self) -> None:
        """The setting is the only reader-visible source of this number.

        It previously had no reader at all: 20,000 was a literal in two request
        models and a third in the dashboard. Patching the setting here proves the
        route follows it rather than a constant that happens to match.
        """
        with patch.object(main.settings, "manual_intake_max_chars", 50):
            response, queue = self._create(text="x" * 51)
            self.assertEqual(response.status_code, 400)
            self.assertIn("50", response.json()["detail"])
            self.assertEqual(queue.calls, [])

            accepted, queue = self._create(text="x" * 50)
            self.assertEqual(accepted.status_code, 200, accepted.text)
            self.assertEqual(len(queue.calls), 1)

    def test_the_preview_route_applies_the_same_cap(self) -> None:
        """Preview runs on blur, so it is where an oversized paste is met first."""
        response = self.client.post(
            "/manual-requirements/preview",
            json={"text": "x" * (main.settings.manual_intake_max_chars + 1)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIsInstance(response.json()["detail"], str)

    # --- B. duplicate warnings ---------------------------------------------

    def test_preview_reports_no_duplicate_for_a_first_paste(self) -> None:
        response = self.client.post("/manual-requirements/preview", json={"text": PASTE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["duplicate_of"])

    def test_preview_names_the_existing_card(self) -> None:
        duplicate = ManualDuplicate(
            id=42, role="Senior Full Stack Developer", client="TECH M", created_at=__import__(
                "datetime"
            ).datetime(2026, 9, 5, tzinfo=__import__("datetime").UTC)
        )
        with patch.object(
            main._get_manual_intake_service(), "preview", return_value=duplicate
        ):
            response = self.client.post("/manual-requirements/preview", json={"text": PASTE})
        body = response.json()["duplicate_of"]
        self.assertEqual(body["id"], 42)
        self.assertEqual(body["role"], "Senior Full Stack Developer")
        self.assertEqual(body["client"], "TECH M")

    def test_a_duplicate_is_a_warning_and_never_a_block(self) -> None:
        """Acknowledged and recorded, never enforced.

        A recruiter re-sending an updated requirement is normal, so the client
        may create anyway and the server must accept it.
        """
        response, queue = self._create(acknowledged_duplicate_of=42)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(queue.calls), 1)

    def test_preview_creates_nothing(self) -> None:
        self.client.post("/manual-requirements/preview", json={"text": PASTE})
        with self.SessionLocal() as db:
            self.assertEqual(db.query(RecruiterEmail).count(), 0)
            self.assertEqual(db.query(RecentRun).count(), 0)


if __name__ == "__main__":
    unittest.main()
