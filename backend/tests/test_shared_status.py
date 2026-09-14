"""C3 (4): every worker must report the same status.

`runtime_state` is a module-level object, so each process has its own. With one
API process that is invisible; with `uvicorn --workers 4`, `/gmail/status`,
`/ai/status` and `/gmail/live-replies` each answer from whichever worker took
the request and the Settings cards flicker between four versions of the truth.

Leader election sharpened it: the auto-runner runs in exactly one process, so
only that process would ever have live-reply data and the other three would
report zero.
"""

import os
import threading
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.config import settings
from app import shared_status as shared_status_module
from app.shared_status import SHARED_FIELDS, SharedStatus, shared_status
from app.runtime_state import AppRuntimeState, runtime_state

try:
    _redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    _redis.ping()
    REDIS_READY = True
except Exception:  # pragma: no cover - depends on the machine, not the code
    _redis = None
    REDIS_READY = False


class FieldSplitTests(unittest.TestCase):
    """What may be shared, and what may never be."""

    def test_handles_are_not_shared(self):
        """A threading.Event in Redis would describe an event, not be one."""
        for name in (
            "telegram_service",
            "telegram_action_lock",
            "auto_runner_thread",
            "auto_runner_stop_event",
            "gmail_labeling_service",
            "telegram_pending_inputs",
            "telegram_auth_sessions",
        ):
            self.assertNotIn(name, SHARED_FIELDS, f"{name} is process state")

    def test_the_status_cards_read_shared_fields(self):
        for name in (
            "last_gmail_sync_at", "ai_running", "ai_last_error",
            "chat_mcp_status", "chat_active_model", "live_replies",
        ):
            self.assertIn(name, SHARED_FIELDS)

    def test_local_handles_stay_usable_objects(self):
        state = AppRuntimeState()
        self.assertIsInstance(state.auto_runner_stop_event, threading.Event)
        state.auto_runner_stop_event.set()
        self.assertTrue(state.auto_runner_stop_event.is_set())

    def test_an_unknown_attribute_still_raises(self):
        """`__getattr__` must not turn every typo into a silent None."""
        with self.assertRaises(AttributeError):
            _ = AppRuntimeState().not_a_field


@unittest.skipUnless(REDIS_READY, "needs the Redis that ships with the stack")
class SharedAcrossProcessesTests(unittest.TestCase):
    def setUp(self):
        shared_status.clear()

    def tearDown(self):
        shared_status.clear()

    def test_a_write_here_is_visible_to_another_process(self):
        """The whole point. A second SharedStatus is what another worker has."""
        runtime_state.chat_mcp_status = "ready"

        other_worker = SharedStatus()
        self.assertEqual(other_worker.get("chat_mcp_status"), "ready")

    def test_a_process_sees_its_own_write_immediately(self):
        """Even though reads are snapshotted for a second."""
        runtime_state.ai_last_error = "first"
        self.assertEqual(runtime_state.ai_last_error, "first")
        runtime_state.ai_last_error = "second"
        self.assertEqual(runtime_state.ai_last_error, "second")

    def test_datetimes_survive_the_round_trip_as_datetimes(self):
        """Tagged rather than guessed: a free-form status string could look
        like a date, and `chat_active_model` is free-form."""
        now = datetime.now(UTC)
        runtime_state.last_gmail_sync_at = now

        other_worker = SharedStatus()
        restored = other_worker.get("last_gmail_sync_at")
        self.assertIsInstance(restored, datetime)
        self.assertEqual(restored, now)

    def test_live_reply_counts_survive_per_owner(self):
        now = datetime.now(UTC)
        runtime_state.live_replies = {"usr_a": (7, now), "usr_b": (0, now)}

        other_worker = SharedStatus()
        restored = other_worker.get("live_replies")
        self.assertEqual(restored["usr_a"][0], 7)
        self.assertEqual(restored["usr_b"][0], 0)
        self.assertEqual(restored["usr_a"][1], now)

    def test_booleans_and_none_are_not_stringified(self):
        runtime_state.ai_running = True
        runtime_state.ai_last_error = None
        other_worker = SharedStatus()
        self.assertIs(other_worker.get("ai_running"), True)
        self.assertIsNone(other_worker.get("ai_last_error"))

    def test_defaults_apply_before_anything_has_been_written(self):
        self.assertEqual(runtime_state.chat_mcp_status, "disabled")
        self.assertIs(runtime_state.ai_running, False)
        self.assertEqual(runtime_state.live_replies, {})

    def test_reads_are_snapshotted_rather_than_one_round_trip_per_field(self):
        """A status card reads a dozen fields and is polled every few seconds."""
        runtime_state.ai_running = True
        shared_status.invalidate()
        with patch.object(
            shared_status_module, "get_redis_connection", wraps=shared_status_module.get_redis_connection
        ) as conn:
            for _ in range(20):
                _ = runtime_state.ai_last_error
                _ = runtime_state.chat_mcp_status
        self.assertLessEqual(conn.call_count, 2, "forty reads must not be forty round trips")


class WithoutRedisTests(unittest.TestCase):
    """Fail open. A status card is a readout, not a guard."""

    def _dead_redis(self):
        return patch.object(
            shared_status_module,
            "get_redis_connection",
            side_effect=RedisConnectionError("no route to host"),
        )

    def test_status_still_reads_and_writes_within_the_process(self):
        state = AppRuntimeState()
        with self._dead_redis():
            state.chat_mcp_status = "ready"
            self.assertEqual(state.chat_mcp_status, "ready")

    def test_defaults_survive_an_outage(self):
        store = SharedStatus()
        with self._dead_redis():
            self.assertIsNone(store.get("never_written"))

    def test_the_redis_url_never_reaches_the_log(self):
        import logging

        store = SharedStatus()
        with self._dead_redis():
            logging.disable(logging.NOTSET)
            with self.assertLogs("app.shared_status", "WARNING") as logs:
                store.get("ai_running")
        joined = "\n".join(logs.output)
        self.assertIn("ConnectionError", joined)
        self.assertNotIn("no route to host", joined)

    def test_an_outage_is_logged_once_not_once_per_poll(self):
        store = SharedStatus()
        with self._dead_redis():
            import logging

            logging.disable(logging.NOTSET)
            with self.assertLogs("app.shared_status", "WARNING") as logs:
                for _ in range(25):
                    store.get("ai_running")
        self.assertEqual(len(logs.output), 1, "this is on a path the dashboard polls")


if __name__ == "__main__":
    unittest.main()
