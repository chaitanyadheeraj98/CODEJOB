"""C1 - admission that holds across processes.

Run against a **real Redis**, not a fake. The thing most likely to be wrong
here is the Lua script, and a fake that reimplements ZADD tests the fake. Redis
is part of the deployment (`docker compose`), so it is available where this
suite runs; the tests skip rather than fail where it is not.

Every test uses a unique pool name, so they share a Redis safely and leave
nothing behind for the next one.
"""

import asyncio
import os
import time
import unittest
import uuid
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app.services import admission_service
from app.services.admission_service import AdmissionRejected

try:
    _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    _redis.ping()
    REDIS_READY = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    _redis = None
    REDIS_READY = False


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.pool = f"test-{uuid.uuid4().hex[:12]}"
        self.held: list = []

    def tearDown(self):
        for lease in self.held:
            admission_service.release(lease, connection=_redis)
        for key in _redis.scan_iter(f"{admission_service.KEY_PREFIX}:{self.pool}:*"):
            _redis.delete(key)
        admission_service.reset_local_semaphores()

    def take(self, owner="usr_a", *, global_limit=10, per_user=10, ttl=60.0, local=3):
        lease = admission_service.acquire(
            self.pool,
            owner_id=owner,
            global_limit=global_limit,
            per_user_limit=per_user,
            ttl_seconds=ttl,
            local_limit=local,
            connection=_redis,
        )
        self.held.append(lease)
        return lease

    def test_a_slot_is_granted_and_counted(self):
        self.take()
        self.assertEqual(admission_service.active_count(self.pool, connection=_redis), 1)

    def test_the_global_cap_refuses_the_next_one(self):
        self.take(global_limit=2)
        self.take(global_limit=2, owner="usr_b")
        with self.assertRaises(AdmissionRejected) as caught:
            self.take(global_limit=2, owner="usr_c")
        self.assertEqual(caught.exception.scope, "global")

    def test_one_user_cannot_take_every_slot(self):
        """The reason a single global semaphore is not enough."""
        self.take(global_limit=10, per_user=2)
        self.take(global_limit=10, per_user=2)
        with self.assertRaises(AdmissionRejected) as caught:
            self.take(global_limit=10, per_user=2)
        self.assertEqual(caught.exception.scope, "user")

        # ...and the next user is unaffected, which is the whole point.
        other = self.take(owner="usr_b", global_limit=10, per_user=2)
        self.assertIsNotNone(other)

    def test_the_user_cap_is_checked_before_the_global_one(self):
        # Both caps are full. The message the user gets should be about their
        # own limit, which is actionable, not about the service being busy.
        self.take(global_limit=2, per_user=2)
        self.take(global_limit=2, per_user=2)
        with self.assertRaises(AdmissionRejected) as caught:
            self.take(global_limit=2, per_user=2)
        self.assertEqual(caught.exception.scope, "user")
        self.assertIn("already have", str(caught.exception))

    def test_releasing_returns_the_slot(self):
        first = self.take(global_limit=1)
        with self.assertRaises(AdmissionRejected):
            self.take(global_limit=1, owner="usr_b")
        admission_service.release(first, connection=_redis)
        self.held.remove(first)
        self.take(global_limit=1, owner="usr_b")

    def test_an_expired_lease_stops_holding_the_slot(self):
        """A worker killed mid-turn must not hold its slot forever."""
        self.take(global_limit=1, ttl=0.4)
        with self.assertRaises(AdmissionRejected):
            self.take(global_limit=1, owner="usr_b")
        time.sleep(0.6)
        # Nobody released anything; the lease simply expired.
        self.take(global_limit=1, owner="usr_b")

    def test_counts_ignore_expired_leases(self):
        self.take(ttl=0.3)
        self.assertEqual(admission_service.active_count(self.pool, connection=_redis), 1)
        time.sleep(0.5)
        self.assertEqual(admission_service.active_count(self.pool, connection=_redis), 0)

    def test_per_user_counts_are_separate(self):
        self.take(owner="usr_a")
        self.take(owner="usr_a")
        self.take(owner="usr_b")
        self.assertEqual(admission_service.active_count(self.pool, owner_id="usr_a", connection=_redis), 2)
        self.assertEqual(admission_service.active_count(self.pool, owner_id="usr_b", connection=_redis), 1)
        self.assertEqual(admission_service.active_count(self.pool, connection=_redis), 3)

    def test_a_double_release_does_not_free_someone_elses_slot(self):
        first = self.take(global_limit=2)
        self.held.remove(first)
        admission_service.release(first, connection=_redis)
        admission_service.release(first, connection=_redis)
        self.take(global_limit=2, owner="usr_b")
        self.take(global_limit=2, owner="usr_c")
        # Two slots, two holders. The repeated release must not have created a
        # third - ZREM of an absent member is a no-op, which is why this holds.
        with self.assertRaises(AdmissionRejected):
            self.take(global_limit=2, owner="usr_d")

    def test_the_cap_is_shared_state_not_process_state(self):
        """The whole point of C1.

        A second, independent client - which is what another uvicorn worker is
        - must see the slot already taken. A module-level semaphore passes
        every other test in this file and fails this one.
        """
        self.take(global_limit=1)
        other_process = Redis.from_url(settings.redis_url)
        try:
            with self.assertRaises(AdmissionRejected):
                admission_service.acquire(
                    self.pool,
                    owner_id="usr_b",
                    global_limit=1,
                    per_user_limit=5,
                    ttl_seconds=60.0,
                    local_limit=3,
                    connection=other_process,
                )
        finally:
            other_process.close()

    def test_the_cap_holds_against_a_burst(self):
        """The race the Lua script exists to close."""
        from concurrent.futures import ThreadPoolExecutor

        def attempt(n: int):
            try:
                return admission_service.acquire(
                    self.pool,
                    owner_id=f"usr_{n}",
                    global_limit=5,
                    per_user_limit=5,
                    ttl_seconds=60.0,
                    local_limit=3,
                    connection=_redis,
                )
            except AdmissionRejected:
                return None

        with ThreadPoolExecutor(max_workers=24) as pool:
            results = list(pool.map(attempt, range(24)))
        granted = [r for r in results if r is not None]
        self.held.extend(granted)
        self.assertEqual(len(granted), 5, "the global cap must hold under concurrency")


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class FailOpenTests(unittest.TestCase):
    """Losing Redis must degrade the cap, not the service."""

    def setUp(self):
        self.pool = f"test-{uuid.uuid4().hex[:12]}"
        admission_service.reset_local_semaphores()

    def tearDown(self):
        admission_service.reset_local_semaphores()

    def _dead_redis(self):
        return patch.object(
            admission_service,
            "get_redis_connection",
            side_effect=RedisConnectionError("no route to host"),
        )

    def test_a_slot_is_still_granted_without_redis(self):
        with self._dead_redis():
            lease = admission_service.acquire(
                self.pool, owner_id="usr_a", global_limit=24, per_user_limit=2,
                ttl_seconds=60.0, local_limit=3,
            )
        self.assertTrue(lease.local)

    def test_the_fallback_caps_at_the_pre_c1_limit(self):
        with self._dead_redis():
            for _ in range(3):
                admission_service.acquire(
                    self.pool, owner_id="usr_a", global_limit=24, per_user_limit=99,
                    ttl_seconds=60.0, local_limit=3,
                )
            with self.assertRaises(AdmissionRejected) as caught:
                admission_service.acquire(
                    self.pool, owner_id="usr_a", global_limit=24, per_user_limit=99,
                    ttl_seconds=60.0, local_limit=3,
                )
        self.assertEqual(caught.exception.scope, "local")

    def test_the_fallback_slot_is_released(self):
        with self._dead_redis():
            lease = admission_service.acquire(
                self.pool, owner_id="usr_a", global_limit=24, per_user_limit=99,
                ttl_seconds=60.0, local_limit=1,
            )
            admission_service.release(lease)
            second = admission_service.acquire(
                self.pool, owner_id="usr_a", global_limit=24, per_user_limit=99,
                ttl_seconds=60.0, local_limit=1,
            )
        self.assertTrue(second.local)

    def test_the_redis_url_never_reaches_the_log(self):
        """Connection errors carry the URL, and the URL may carry a password."""
        with self._dead_redis(), self.assertLogs("app.services.admission_service", "WARNING") as logs:
            admission_service.acquire(
                self.pool, owner_id="usr_a", global_limit=24, per_user_limit=2,
                ttl_seconds=60.0, local_limit=3,
            )
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("no route to host", joined)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class WaitingForASlotTests(unittest.IsolatedAsyncioTestCase):
    """C2 - queue for a slot rather than refusing on sight.

    A 503 to someone who would have waited two seconds is a worse answer than
    the wait. 503 stays as the *timeout*, not the first response.
    """

    def setUp(self):
        self.pool = f"test-{uuid.uuid4().hex[:12]}"
        self.held: list = []

    def tearDown(self):
        for lease in self.held:
            admission_service.release(lease, connection=_redis)
        for key in _redis.scan_iter(f"{admission_service.KEY_PREFIX}:{self.pool}:*"):
            _redis.delete(key)
        admission_service.reset_local_semaphores()

    def fill(self, owner="usr_a", *, global_limit=1, per_user=5):
        lease = admission_service.acquire(
            self.pool, owner_id=owner, global_limit=global_limit,
            per_user_limit=per_user, ttl_seconds=60.0, local_limit=3, connection=_redis,
        )
        self.held.append(lease)
        return lease

    async def wait_for(self, owner="usr_b", *, wait=1.0, global_limit=1, per_user=5):
        return await admission_service.acquire_async(
            self.pool, owner_id=owner, global_limit=global_limit, per_user_limit=per_user,
            ttl_seconds=60.0, local_limit=3, wait_seconds=wait, poll_seconds=0.05,
            connection=_redis,
        )

    async def test_a_waiter_is_admitted_when_a_slot_frees(self):
        blocker = self.fill()

        async def release_shortly():
            await asyncio.sleep(0.2)
            admission_service.release(blocker, connection=_redis)
            self.held.remove(blocker)

        releaser = asyncio.create_task(release_shortly())
        lease = await self.wait_for(wait=3.0)
        self.held.append(lease)
        await releaser
        self.assertFalse(lease.local)

    async def test_the_wait_is_bounded_and_ends_in_a_refusal(self):
        self.fill()
        started = time.monotonic()
        with self.assertRaises(AdmissionRejected) as caught:
            await self.wait_for(wait=0.4)
        elapsed = time.monotonic() - started
        self.assertEqual(caught.exception.scope, "global")
        self.assertGreaterEqual(elapsed, 0.2, "it should actually have waited")
        self.assertLess(elapsed, 3.0, "and not much beyond the deadline")

    async def test_no_wait_refuses_immediately(self):
        """C1 behaviour must remain available and remain the default."""
        self.fill()
        started = time.monotonic()
        with self.assertRaises(AdmissionRejected):
            await self.wait_for(wait=0.0)
        self.assertLess(time.monotonic() - started, 0.2)

    async def test_a_per_user_refusal_does_not_wait(self):
        """Waiting cannot help: the caller's own turn has to finish first.

        Holding the request open for ten seconds would pretend otherwise.
        """
        self.fill(owner="usr_a", global_limit=10, per_user=1)
        started = time.monotonic()
        with self.assertRaises(AdmissionRejected) as caught:
            await self.wait_for(owner="usr_a", wait=5.0, global_limit=10, per_user=1)
        self.assertEqual(caught.exception.scope, "user")
        self.assertLess(time.monotonic() - started, 0.5, "a per-user refusal must be immediate")

    async def test_waiting_does_not_block_the_event_loop(self):
        """The Redis call runs in a thread; the wait must stay cooperative."""
        self.fill()
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(ticker())
        with self.assertRaises(AdmissionRejected):
            await self.wait_for(wait=0.6)
        beat.cancel()
        self.assertGreater(ticks, 3, "other coroutines must keep running while one waits")
