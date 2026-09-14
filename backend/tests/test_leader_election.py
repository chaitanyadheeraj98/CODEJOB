"""Exactly one process runs the background work.

`StartupService.startup()` runs in every process, so `uvicorn --workers 4`
would start four auto-runners and four Telegram pollers. The auto-runner sends
email; four copies is duplicate outward-facing action that no retry undoes. Two
Telegram pollers on one token is a 409 from Telegram with updates split between
them.

Run against a real Redis, for the same reason the admission tests are: the Lua
script is the part most likely to be wrong, and a fake would be testing itself.
"""

import os
import threading
import time
import unittest
import uuid
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.services import leader_election

try:
    _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    _redis.ping()
    REDIS_READY = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    _redis = None
    REDIS_READY = False


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class LeaderElectionTests(unittest.TestCase):
    def setUp(self):
        self.role = f"test-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        _redis.delete(f"{leader_election.KEY_PREFIX}:{self.role}")

    def _as(self, instance: str, fn, *args, **kwargs):
        """Run `fn` as if this were a different process."""
        return fn(*args, instance_id=instance, **kwargs)

    def test_the_first_caller_becomes_the_leader(self):
        self.assertTrue(leader_election.is_leader(self.role, connection=_redis))

    def test_a_second_process_does_not(self):
        """The whole point: four workers, one auto-runner."""
        self.assertTrue(self._as("worker-1", leader_election.is_leader, self.role, connection=_redis))
        self.assertFalse(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))
        self.assertFalse(self._as("worker-3", leader_election.is_leader, self.role, connection=_redis))

    def test_the_leader_keeps_it_across_ticks(self):
        for _ in range(5):
            self.assertTrue(self._as("worker-1", leader_election.is_leader, self.role, connection=_redis))
        self.assertFalse(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))

    def test_renewing_extends_the_lease_rather_than_letting_it_lapse(self):
        self._as("worker-1", leader_election.is_leader, self.role, ttl_seconds=2, connection=_redis)
        time.sleep(1.2)
        self._as("worker-1", leader_election.is_leader, self.role, ttl_seconds=2, connection=_redis)
        time.sleep(1.2)
        # Past the original two seconds; still held because it was renewed.
        self.assertFalse(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))

    def test_a_dead_leader_is_replaced_without_anyone_intervening(self):
        self._as("worker-1", leader_election.is_leader, self.role, ttl_seconds=1, connection=_redis)
        self.assertFalse(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))
        time.sleep(1.3)  # worker-1 stops renewing, as a killed process would
        self.assertTrue(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))

    def test_releasing_hands_over_immediately(self):
        """A rolling restart should not leave a gap as long as the TTL."""
        self._as("worker-1", leader_election.is_leader, self.role, connection=_redis)
        self._as("worker-1", leader_election.release, self.role, connection=_redis)
        self.assertTrue(self._as("worker-2", leader_election.is_leader, self.role, connection=_redis))

    def test_a_process_cannot_release_someone_elses_lease(self):
        """An expired-then-retaken lease must not be deleted by the old holder."""
        self._as("worker-1", leader_election.is_leader, self.role, connection=_redis)
        self._as("worker-2", leader_election.release, self.role, connection=_redis)
        self.assertTrue(self._as("worker-1", leader_election.is_leader, self.role, connection=_redis))
        self.assertFalse(self._as("worker-3", leader_election.is_leader, self.role, connection=_redis))

    def test_roles_are_independent(self):
        other = f"{self.role}-other"
        try:
            self.assertTrue(self._as("worker-1", leader_election.is_leader, self.role, connection=_redis))
            self.assertTrue(self._as("worker-2", leader_election.is_leader, other, connection=_redis))
        finally:
            _redis.delete(f"{leader_election.KEY_PREFIX}:{other}")

    def test_only_one_of_a_simultaneous_crowd_wins(self):
        """Start-up is exactly this shape: N processes racing at once."""
        from concurrent.futures import ThreadPoolExecutor

        def claim(n: int) -> bool:
            return leader_election.is_leader(
                self.role, instance_id=f"worker-{n}", connection=_redis
            )

        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(claim, range(16)))
        self.assertEqual(sum(results), 1, "exactly one process may hold the lease")


class LeaderElectionWithoutRedisTests(unittest.TestCase):
    """No Redis, no leader - and that is the safe answer."""

    def _dead_redis(self):
        return patch.object(
            leader_election,
            "get_redis_connection",
            side_effect=RedisConnectionError("no route to host"),
        )

    def test_nobody_leads_when_redis_is_unreachable(self):
        """Failing open would mean four processes each sending the same email.

        A paused auto-runner is recoverable; a recruiter receiving the same
        message twice is not.
        """
        with self._dead_redis():
            self.assertFalse(leader_election.is_leader("any_role"))

    def test_releasing_without_redis_does_not_raise(self):
        with self._dead_redis():
            leader_election.release("any_role")

    def test_the_redis_url_never_reaches_the_log(self):
        import logging

        with self._dead_redis():
            logging.disable(logging.NOTSET)
            with self.assertLogs("app.services.leader_election", "WARNING") as logs:
                leader_election.is_leader("any_role")
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("no route to host", joined)


class InstanceIdentityTests(unittest.TestCase):
    def test_two_processes_would_not_share_an_identity(self):
        """The pid alone repeats across containers, which would be a disaster."""
        self.assertNotEqual(leader_election.INSTANCE_ID.split("-", 1)[1], "")
        self.assertIn(str(os.getpid()), leader_election.INSTANCE_ID)


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class AutoRunnerLeadershipTests(unittest.TestCase):
    """The loop must stand by rather than work when it is not the leader."""

    def test_a_non_leader_tick_does_nothing(self):
        from app.services.auto_runner_service import AutoRunnerService

        called: list[str] = []

        class _StopAfter(threading.Event):
            def __init__(self, ticks):
                super().__init__()
                self.remaining = ticks

            def wait(self, timeout=None):  # noqa: ARG002
                if self.remaining <= 0:
                    return True
                self.remaining -= 1
                return False

        service = AutoRunnerService(
            session_factory=lambda: None,
            get_settings=lambda _db: None,
            run_once=lambda *_a: None,
            run_nvoids_once=lambda *_a: None,
            check_live_replies=lambda _db: None,
            run_reminder_sweep=lambda _db: None,
            run_resume_tracking_sweep=lambda _db: None,
            action_lock=threading.Lock(),
            stop_event=_StopAfter(3),
            list_owners=lambda: called.append("listed") or ["usr_a"],
            is_leader=lambda: False,
        )
        service.run_loop()
        self.assertEqual(called, [], "a non-leader must not even look for tenants")


if __name__ == "__main__":
    unittest.main()
