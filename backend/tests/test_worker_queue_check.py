"""The worker consumes every queue the application can enqueue to.

This is the guard for a failure with no symptom. `get_queue` accepts any name in
QUEUE_NAMES, the enqueue endpoint returns 200 and writes a RecentRun, and the
job then sits in Redis forever because no worker is listening. No error, no log,
no failing unit test - a fake queue in a test always succeeds.

So the check is a text comparison between two files that must agree and have no
other link, and these tests check the checker: that it passes today, and that it
would actually fail if the two drifted apart.
"""

import unittest

from scripts.check_worker_queues import declared_queues, main, worker_queues

COMPOSE_SNIPPET = """  worker:
    command: rq worker gmail_sync nvoids_sync --url redis://redis:6379/0 --with-scheduler
"""

QUEUES_SNIPPET = '''
GMAIL_SYNC_QUEUE = "gmail_sync"
NVOIDS_SYNC_QUEUE = "nvoids_sync"
MANUAL_INTAKE_QUEUE = "manual_intake"
QUEUE_NAMES = frozenset({GMAIL_SYNC_QUEUE, NVOIDS_SYNC_QUEUE, MANUAL_INTAKE_QUEUE})
'''


class WorkerQueueCheckTests(unittest.TestCase):
    def test_the_real_files_agree(self) -> None:
        """The check that actually protects the deployment."""
        self.assertEqual(main(), 0)

    def test_the_worker_command_is_parsed_not_guessed(self) -> None:
        self.assertEqual(worker_queues(COMPOSE_SNIPPET), ["gmail_sync", "nvoids_sync"])

    def test_only_the_constants_are_read_not_the_frozenset_body(self) -> None:
        """QUEUE_NAMES repeats the names; counting those would hide a drift."""
        self.assertEqual(
            declared_queues(QUEUES_SNIPPET),
            ["gmail_sync", "nvoids_sync", "manual_intake"],
        )

    def test_a_queue_no_worker_consumes_is_detected(self) -> None:
        """Guards the guard: manual_intake declared, absent from the command."""
        declared = set(declared_queues(QUEUES_SNIPPET))
        consumed = set(worker_queues(COMPOSE_SNIPPET))
        self.assertEqual(sorted(declared - consumed), ["manual_intake"])


if __name__ == "__main__":
    unittest.main()
