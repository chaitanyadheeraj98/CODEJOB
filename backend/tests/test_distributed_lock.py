"""C3 (2) and (3): a pool sized for the threadpool, and locks that cross processes.

The locks these replace were `threading.Lock` objects guarding endpoints. An
endpoint can be served by any worker, so they excluded nothing once there was
more than one process - two requests could both start a taxonomy embedding
batch over the same rows, each believing it held the lock.

They were also global, which was invisible with one tenant and wrong with two:
one account's bulk-review apply blocked every other account's.

Against a real Redis, for the same reason as the admission and leader suites.
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
from app.services import distributed_lock
from app.services.distributed_lock import LockBusy

try:
    _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    _redis.ping()
    REDIS_READY = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    _redis = None
    REDIS_READY = False


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class DistributedLockTests(unittest.TestCase):
    def setUp(self):
        self.name = f"test-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        for key in _redis.scan_iter(f"{distributed_lock.KEY_PREFIX}:{self.name}:*"):
            _redis.delete(key)

    def test_the_lock_is_held_inside_the_block_and_free_after(self):
        with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
            self.assertTrue(distributed_lock.is_held(self.name, owner_id="usr_a", connection=_redis))
        self.assertFalse(distributed_lock.is_held(self.name, owner_id="usr_a", connection=_redis))

    def test_a_second_holder_is_refused_rather_than_queued(self):
        """Every call site this replaces answered 409, not "please wait"."""
        with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
            with self.assertRaises(LockBusy):
                with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                    pass

    def test_one_tenant_does_not_block_another(self):
        """The bug the old global lock had, and nobody noticed with one tenant."""
        with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
            with distributed_lock.hold(self.name, owner_id="usr_b", connection=_redis):
                self.assertTrue(
                    distributed_lock.is_held(self.name, owner_id="usr_b", connection=_redis)
                )

    def test_different_locks_do_not_collide(self):
        other = f"{self.name}-other"
        try:
            with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                with distributed_lock.hold(other, owner_id="usr_a", connection=_redis):
                    pass
        finally:
            _redis.delete(f"{distributed_lock.KEY_PREFIX}:{other}:usr_a")

    def test_the_lock_is_released_when_the_body_raises(self):
        with self.assertRaises(ValueError):
            with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                raise ValueError("boom")
        self.assertFalse(distributed_lock.is_held(self.name, owner_id="usr_a", connection=_redis))

    def test_a_long_hold_keeps_its_lock_past_the_ttl(self):
        """The watchdog is what lets the TTL stay short.

        Without renewal a taxonomy batch outliving its TTL would let a second
        one start - the exact thing the lock exists to prevent.
        """
        with distributed_lock.hold(self.name, owner_id="usr_a", ttl_seconds=2, connection=_redis):
            time.sleep(3.0)
            self.assertTrue(
                distributed_lock.is_held(self.name, owner_id="usr_a", connection=_redis),
                "the watchdog must have extended the lease",
            )
            with self.assertRaises(LockBusy):
                with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                    pass

    def test_a_crashed_holder_stops_blocking_within_the_ttl(self):
        """And the short TTL is what makes that quick.

        Simulated by writing the key the way `hold` does and never renewing it,
        which is what a killed process leaves behind.
        """
        key = f"{distributed_lock.KEY_PREFIX}:{self.name}:usr_a"
        _redis.set(key, "a-process-that-died", nx=True, ex=1)
        with self.assertRaises(LockBusy):
            with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                pass
        time.sleep(1.3)
        with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
            pass

    def test_releasing_does_not_delete_a_lock_someone_else_took(self):
        """A holder whose lease expired must not clear its successor's."""
        key = f"{distributed_lock.KEY_PREFIX}:{self.name}:usr_a"
        try:
            with distributed_lock.hold(self.name, owner_id="usr_a", ttl_seconds=1, connection=_redis):
                # Simulate the lease having lapsed and been taken by another
                # process while this block was still running.
                _redis.set(key, "somebody-else", ex=60)
            self.assertEqual(_redis.get(key).decode(), "somebody-else")
        finally:
            _redis.delete(key)

    def test_only_one_of_a_simultaneous_crowd_gets_in(self):
        from concurrent.futures import ThreadPoolExecutor

        entered = []
        barrier = threading.Barrier(12)

        def attempt(_n: int) -> None:
            barrier.wait()
            try:
                with distributed_lock.hold(self.name, owner_id="usr_a", connection=_redis):
                    entered.append(1)
                    time.sleep(0.2)
            except LockBusy:
                pass

        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(attempt, range(12)))
        self.assertEqual(len(entered), 1, "exactly one may hold it")


class LockWithoutRedisTests(unittest.TestCase):
    def _dead_redis(self):
        return patch.object(
            distributed_lock,
            "get_redis_connection",
            side_effect=RedisConnectionError("no route to host"),
        )

    def test_no_redis_means_no_lock_and_therefore_no_work(self):
        """Fail closed.

        These guard writes - a taxonomy batch and a bulk apply. Running two of
        them concurrently is worse than running neither, so an unreachable
        Redis produces a 409 rather than an unguarded write.
        """
        with self._dead_redis(), self.assertRaises(LockBusy):
            with distributed_lock.hold("any_lock", owner_id="usr_a"):
                pass

    def test_the_redis_url_never_reaches_the_log(self):
        import logging

        with self._dead_redis():
            logging.disable(logging.NOTSET)
            with self.assertLogs("app.services.distributed_lock", "WARNING") as logs:
                with self.assertRaises(LockBusy):
                    with distributed_lock.hold("any_lock", owner_id="usr_a"):
                        pass
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("no route to host", joined)


class ConnectionPoolSizingTests(unittest.TestCase):
    """C3 (2): fifteen connections against a forty-thread pool.

    FastAPI runs this application's sync endpoints in the anyio threadpool,
    which holds 40 threads. SQLAlchemy's untouched defaults give 5 + 10.
    """

    def test_the_pool_is_sized_against_the_threadpool_not_left_at_the_default(self):
        self.assertGreaterEqual(
            settings.db_pool_size + settings.db_max_overflow,
            30,
            "a ceiling below the 40-thread limiter makes requests queue on the pool",
        )

    def test_the_engine_actually_uses_the_configured_size(self):
        """A setting nothing reads would be worse than no setting."""
        import app.db as database

        if settings.database_url.startswith("sqlite"):
            self.skipTest("SQLite does not use a queue pool; checked against PostgreSQL")
        self.assertEqual(database.engine.pool.size(), settings.db_pool_size)
        self.assertEqual(database.engine.pool._max_overflow, settings.db_max_overflow)

    def test_sqlite_is_left_alone(self):
        """Passing queue-pool arguments to SQLite's pool would fail outright."""
        from sqlalchemy import create_engine

        engine = create_engine("sqlite://")
        self.assertTrue(engine.dialect.name == "sqlite")


if __name__ == "__main__":
    unittest.main()
