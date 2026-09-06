"""The endpoint a confirmed `propose_nvoids_search` card posts to.

It did not exist. `_enqueue_nvoids_client_search` was written, given its own
run-key prefix so `check_nvoids_search` could find the run, and never routed -
so the tool proposed searches with nothing to confirm to, and the dashboard had
no handler for the payload either. Both halves are covered here and in
`proposals.nvoids.test.ts`.

The test that matters most is `test_the_query_is_recomposed_and_never_taken_from
_the_request`. A search is an outbound request to a third party, and the card
shows a `generated_query` string for the user to read. If the request could
carry that string back and have it used, a display field would have become the
instruction, and the query actually sent would be one nothing on screen had to
agree with.
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
from app.models import RecentRun, UserSettings
from app.recent_runs import NVOIDS_CLIENT_SEARCH_PREFIX

CRITERIA = {
    "end_client": "Morgan Stanley",
    "job_role": "Java Developer",
    "search_location": "Texas",
    "query_mode": "composed",
    "batch_limit": 10,
}


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        return SimpleNamespace(id=kwargs["job_id"])


class NvoidsClientSearchRouteTests(unittest.TestCase):
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
            db.add(UserSettings(owner_id=main.settings.owner_id, feature_nvoids_enabled=True))
            db.commit()
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _post(self, **body):
        queue = _FakeQueue()
        with (
            patch.object(main, "active_job_id", return_value=None),
            patch.object(main, "get_queue", return_value=queue),
        ):
            response = self.client.post("/jobs/nvoids-client-search", json={**CRITERIA, **body})
        return response, queue

    def _criteria_sent(self, queue: _FakeQueue) -> dict:
        return queue.calls[0]["kwargs"]["criteria"]

    def test_a_confirmed_card_queues_one_search(self) -> None:
        response, queue = self._post()
        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["run_key"].startswith(NVOIDS_CLIENT_SEARCH_PREFIX))
        self.assertEqual(len(queue.calls), 1)

    def test_the_run_carries_the_client_search_prefix(self) -> None:
        """`check_nvoids_search` finds the run the user started by this prefix.

        On the scheduled sync's prefix it would report whichever sync ran last,
        which is a different search and possibly nobody's.
        """
        response, _ = self._post()
        with self.SessionLocal() as db:
            row = db.query(RecentRun).filter_by(run_key=response.json()["run_key"]).one()
        self.assertTrue(row.run_key.startswith(NVOIDS_CLIENT_SEARCH_PREFIX))
        self.assertEqual(row.status, "queued")

    def test_the_criteria_reach_the_worker(self) -> None:
        _, queue = self._post()
        criteria = self._criteria_sent(queue)
        self.assertEqual(criteria["end_client"], "Morgan Stanley")
        self.assertEqual(criteria["job_role"], "Java Developer")
        self.assertEqual(criteria["search_location"], "Texas")
        self.assertEqual(criteria["query_mode"], "composed")

    def test_the_query_is_recomposed_and_never_taken_from_the_request(self) -> None:
        """`generated_query` is display-only, and the model has no field for it.

        Sent anyway, it is ignored: the request model drops it and the route
        composes the query from the criteria it did accept.
        """
        _, queue = self._post(generated_query="anything at all")
        criteria = self._criteria_sent(queue)
        query = criteria["generated_query"]
        self.assertNotEqual(query, "anything at all")
        # Composed server-side from the criteria, which is why the company
        # arrives as nvoids' own clause form rather than as it was typed.
        self.assertIn("morgan", query)
        self.assertIn("stanley", query)
        self.assertIn("Java Developer", query)

    def test_end_client_only_mode_drops_role_and_location(self) -> None:
        """Discovery mode. Composing all three narrows to almost nothing, which
        is why the tool offers the mode at all."""
        _, queue = self._post(query_mode="end_client_only")
        criteria = self._criteria_sent(queue)
        self.assertEqual(criteria["query_mode"], "end_client_only")
        self.assertNotIn("Java Developer", criteria["generated_query"])

    def test_an_unknown_query_mode_is_refused(self) -> None:
        response, queue = self._post(query_mode="everything")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(queue.calls, [])

    def test_an_empty_end_client_is_refused(self) -> None:
        """Without one this is the scheduled sync wearing a search's run key."""
        response, queue = self._post(end_client="")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(queue.calls, [])

    def test_the_batch_limit_is_bounded(self) -> None:
        over, queue = self._post(batch_limit=5000)
        self.assertEqual(over.status_code, 422)
        self.assertEqual(queue.calls, [])

        under, queue = self._post(batch_limit=0)
        self.assertEqual(under.status_code, 422)
        self.assertEqual(queue.calls, [])

    def test_the_feature_switch_is_honoured(self) -> None:
        """The same check the scheduled sync route makes. A disabled feature that
        the assistant can still reach is not disabled."""
        with self.SessionLocal() as db:
            row = db.query(UserSettings).filter_by(owner_id=main.settings.owner_id).one()
            row.feature_nvoids_enabled = False
            db.commit()

        response, queue = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("disabled", response.json()["detail"])
        self.assertEqual(queue.calls, [])

    def test_surrounding_whitespace_is_trimmed_before_the_query_is_built(self) -> None:
        _, queue = self._post(end_client="  Morgan Stanley  ")
        self.assertEqual(self._criteria_sent(queue)["end_client"], "Morgan Stanley")


if __name__ == "__main__":
    unittest.main()
