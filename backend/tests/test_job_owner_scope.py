"""§16 test 3 — a background job runs as the owner who enqueued it.

The sharpest edge in the whole multi-tenant change. A job has no request and
therefore no middleware, so without the owner travelling in its payload
`tenancy.owner_id()` falls back to the configured constant: one user's job
quietly writes into another account, and nothing raises. The silence is what
makes it dangerous, and it is why this is enforced by a decorator plus a test
that enumerates the module, rather than by remembering.
"""

import inspect
import unittest

from app import tenancy
from app.config import settings
from app.jobs import tasks


class DecoratorCoverageTests(unittest.TestCase):
    """Forgetting the decorator must fail here, not in production."""

    def _job_functions(self):
        return [
            (name, value)
            for name, value in vars(tasks).items()
            if name.startswith("run_") and name.endswith("_job") and inspect.isfunction(value)
        ]

    def test_there_are_job_functions_to_check(self) -> None:
        """Guards against the enumeration silently matching nothing."""
        self.assertGreaterEqual(len(self._job_functions()), 5)

    def test_every_job_function_is_owner_scoped(self) -> None:
        undecorated = [name for name, fn in self._job_functions() if not getattr(fn, "__owner_scoped__", False)]

        self.assertEqual(undecorated, [], "these jobs would run as the configured owner")


class OwnerScopeBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.seen: list[str] = []

        @tenancy.owner_scoped
        def job(**kwargs):
            self.seen.append(tenancy.owner_id())
            return kwargs

        self.job = job

    def test_the_job_runs_as_the_owner_in_its_payload(self) -> None:
        self.job(owner_id="usr_alice")

        self.assertEqual(self.seen, ["usr_alice"])

    def test_the_owner_is_not_passed_through_to_the_task_body(self) -> None:
        """Task signatures stay unchanged; only the wrapper knows about it."""
        result = self.job(owner_id="usr_alice", record_id=7)

        self.assertEqual(result, {"record_id": 7})

    def test_a_job_with_no_owner_falls_back_rather_than_crashing(self) -> None:
        """Anything already queued when this deployed has no owner in it."""
        self.job(record_id=7)

        self.assertEqual(self.seen, [settings.owner_id])

    def test_the_owner_does_not_leak_into_the_next_job(self) -> None:
        self.job(owner_id="usr_alice")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_the_owner_is_restored_even_when_the_job_raises(self) -> None:
        @tenancy.owner_scoped
        def failing(**kwargs):
            raise RuntimeError("job failed")

        with self.assertRaises(RuntimeError):
            failing(owner_id="usr_alice")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_two_jobs_in_sequence_each_get_their_own_owner(self) -> None:
        self.job(owner_id="usr_alice")
        self.job(owner_id="usr_bob")

        self.assertEqual(self.seen, ["usr_alice", "usr_bob"])

    def test_the_wrapper_keeps_the_name_rq_serialises(self) -> None:
        """RQ stores a job by import path; a lost __name__ breaks dispatch."""
        self.assertEqual(self.job.__name__, "job")
        self.assertEqual(tasks.run_gmail_sync_job.__name__, "run_gmail_sync_job")


class EnqueueSiteTests(unittest.TestCase):
    """Every enqueue must put the owner in the payload, or the decorator has nothing to read."""

    def test_every_enqueue_call_passes_an_owner(self) -> None:
        import pathlib
        import re

        offenders: list[str] = []
        for path in pathlib.Path("app").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"\.enqueue\(", text):
                # Read the call through to its closing bracket.
                start = match.end()
                depth, index = 1, start
                while index < len(text) and depth:
                    depth += (text[index] == "(") - (text[index] == ")")
                    index += 1
                if "owner_id" not in text[start:index]:
                    line = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{path.as_posix()}:{line}")

        self.assertEqual(offenders, [], "these enqueues would run as the configured owner")


if __name__ == "__main__":
    unittest.main()
