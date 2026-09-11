"""C1 - "stop" has to reach the turn, not just the process that was asked.

With one API process this is a dictionary lookup and always works. With four,
three stop requests in four land on a process whose `active_turns` is empty:
404 to the user, while the turn keeps running and keeps costing.

Two processes are simulated by clearing `active_turns`, which is exactly what
the other process's copy looks like: the Redis registration is the only thing
they share.
"""

import asyncio
import logging
import os
import unittest
import uuid
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.ai.chat import turns
from app.config import settings

try:
    _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    _redis.ping()
    REDIS_READY = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    _redis = None
    REDIS_READY = False


async def _forever():
    while True:
        await asyncio.sleep(0.05)
        yield "tick"


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class CrossWorkerCancelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        self.session_id = 4242

    def tearDown(self):
        turns.active_turns.pop(self.turn_id, None)
        _redis.delete(turns._key(self.turn_id))

    async def test_a_turn_registers_itself_where_others_can_see_it(self):
        turn = turns.start(self.session_id, self.turn_id, _forever())
        try:
            stored = _redis.hget(turns._key(self.turn_id), "session_id")
            self.assertIsNotNone(stored, "another process must be able to find this turn")
            self.assertEqual(int(stored), self.session_id)
        finally:
            await turns.close(self.turn_id)

    async def test_cancel_from_another_process_stops_the_turn(self):
        turn = turns.start(self.session_id, self.turn_id, _forever())
        try:
            # The other worker: same Redis, no local record of this turn.
            local = dict(turns.active_turns)
            turns.active_turns.clear()
            self.assertTrue(
                turns.cancel(self.session_id, self.turn_id),
                "a turn known to Redis must be cancellable from any process",
            )
            turns.active_turns.update(local)

            # ...and the process actually running it notices, via the watcher.
            for _ in range(40):
                await asyncio.sleep(turns.CANCEL_POLL_SECONDS / 2)
                if turn.cancelled:
                    break
            self.assertTrue(turn.cancelled, "the streaming process must act on the flag")
            self.assertTrue(turn.task.cancelled() or turn.task.done())
        finally:
            await turns.close(self.turn_id)

    async def test_a_turn_id_is_not_permission_to_stop_someone_elses_turn(self):
        turns.start(self.session_id, self.turn_id, _forever())
        try:
            turns.active_turns.clear()
            self.assertFalse(turns.cancel(self.session_id + 1, self.turn_id))
            self.assertNotEqual(int(_redis.hget(turns._key(self.turn_id), "cancelled")), 1)
        finally:
            await turns.close(self.turn_id)

    async def test_an_unknown_turn_is_not_reported_as_cancelled(self):
        self.assertFalse(turns.cancel(self.session_id, "turn-never-existed"))

    async def test_closing_a_turn_removes_it_from_redis(self):
        turns.start(self.session_id, self.turn_id, _forever())
        await turns.close(self.turn_id)
        self.assertFalse(_redis.exists(turns._key(self.turn_id)))

    async def test_the_local_path_still_cancels_immediately(self):
        """The common case must not wait for a poll interval."""
        turn = turns.start(self.session_id, self.turn_id, _forever())
        try:
            self.assertTrue(turns.cancel(self.session_id, self.turn_id))
            self.assertTrue(turn.cancelled, "no polling should be involved locally")
        finally:
            await turns.close(self.turn_id)


class RedisDownTests(unittest.IsolatedAsyncioTestCase):
    """Without Redis this must behave exactly as it did before C1."""

    def setUp(self):
        self.turn_id = f"turn-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        turns.active_turns.pop(self.turn_id, None)

    def _dead_redis(self):
        return patch.object(
            turns, "get_redis_connection", side_effect=RedisConnectionError("no route to host")
        )

    async def test_a_turn_still_starts_and_cancels_locally(self):
        with self._dead_redis():
            turn = turns.start(7, self.turn_id, _forever())
            try:
                self.assertTrue(turns.cancel(7, self.turn_id))
                self.assertTrue(turn.cancelled)
            finally:
                await turns.close(self.turn_id)

    async def test_cross_process_cancel_reports_failure_rather_than_lying(self):
        with self._dead_redis():
            turns.start(7, self.turn_id, _forever())
            try:
                turns.active_turns.clear()
                self.assertFalse(turns.cancel(7, self.turn_id))
            finally:
                await turns.close(self.turn_id)

    async def test_the_redis_url_never_reaches_the_log(self):
        with self._dead_redis():
            # The premise, asserted rather than assumed. A patch that silently
            # failed to apply would let `register` succeed against a real
            # Redis, emit nothing, and fail below as "no logs triggered" -
            # which reads like a logging fault and is not one.
            with self.assertRaises(RedisConnectionError):
                turns.get_redis_connection()
            # An earlier test may have left logging disabled process-wide, and
            # assertLogs does not reset that. A secret-leak guard must not
            # depend on the state the rest of the suite happened to leave.
            logging.disable(logging.NOTSET)
            with self.assertLogs("app.ai.chat.turns", "WARNING") as logs:
                turns.register(self.turn_id, 7)
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("no route to host", joined)


if __name__ == "__main__":
    unittest.main()
