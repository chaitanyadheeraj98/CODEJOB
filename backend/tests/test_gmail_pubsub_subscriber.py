"""What the subscriber acknowledges, what it defers, and who it writes for.

Two failure modes shape this file.

**Acknowledging too little.** A message nobody can ever process - malformed, or
for a mailbox this installation has never connected - must be acknowledged. Left
unacknowledged it redelivers forever, and a growing backlog of poison is what
eventually delays the valid ones.

**Acknowledging too much.** A message dropped because Redis happened to be down
is a reply that never arrives, with nothing anywhere saying so. That case, and
only that case, is nacked.

Nothing here needs the Google client or a credential: the parsing, routing and
acknowledgement decisions are ordinary functions, which is why they are.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as app_db
from app import gmail_pubsub_subscriber as subscriber
from app.config import settings
from app.db import Base
from app.jobs.queues import GMAIL_EVENT_QUEUE
from app.models import GmailCredential, User, UserSettings
from app.services import distributed_lock, gmail_pubsub_service

MINE = "usr_mine"
THEIRS = "usr_theirs"
MY_ADDRESS = "me@example.com"


class _Message:
    """The two methods a Pub/Sub message has that this code uses."""

    def __init__(self, payload):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode("utf-8")
        self.data = payload
        self.acked = False
        self.nacked = False

    def ack(self):
        self.acked = True

    def nack(self):
        self.nacked = True


class _Queue:
    def __init__(self):
        self.jobs = []

    def enqueue(self, func, **kwargs):
        self.jobs.append((func, kwargs))
        return type("Job", (), {"id": "job-1"})()


class ParseTests(unittest.TestCase):
    def test_a_valid_payload_is_normalised(self):
        parsed = subscriber.parse_notification(
            json.dumps({"emailAddress": "  Me@Example.COM ", "historyId": "4821"}).encode()
        )

        self.assertEqual(parsed.email_address, "me@example.com")
        self.assertEqual(parsed.history_id, "4821")

    def test_a_numeric_history_id_is_accepted(self):
        """Gmail sends it as a JSON number or a string depending on nothing in
        particular. Both normalise to the decimal string treated as opaque."""
        parsed = subscriber.parse_notification(
            json.dumps({"emailAddress": MY_ADDRESS, "historyId": 4821}).encode()
        )

        self.assertEqual(parsed.history_id, "4821")

    def test_the_payload_is_not_base64_decoded_again(self):
        """The client library already decoded it. A second decode would fail on
        every valid message, and this is the obvious 'fix' someone reaches for
        because the wire format really is base64."""
        import base64

        raw = json.dumps({"emailAddress": MY_ADDRESS, "historyId": "1"}).encode()

        self.assertIsNotNone(subscriber.parse_notification(raw))
        self.assertIsNone(subscriber.parse_notification(base64.b64encode(raw)))

    def test_rubbish_is_refused(self):
        for payload in (
            b"", b"not json", b"[]", b"null",
            json.dumps({"historyId": "1"}).encode(),
            json.dumps({"emailAddress": MY_ADDRESS}).encode(),
            json.dumps({"emailAddress": "", "historyId": "1"}).encode(),
            json.dumps({"emailAddress": MY_ADDRESS, "historyId": "not-a-number"}).encode(),
            b"\xff\xfe binary",
        ):
            with self.subTest(payload=payload[:30]):
                self.assertIsNone(subscriber.parse_notification(payload))


class _Base(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.maker = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.maker()
        self.queue = _Queue()

        @contextmanager
        def scope():
            session = self.maker()
            try:
                yield session
                session.commit()
            finally:
                session.close()

        self._patches = [
            patch.object(subscriber, "session_scope", scope),
            patch.object(gmail_pubsub_service, "session_scope", scope),
            patch.object(subscriber, "get_queue", lambda name: self.queue),
            patch.object(settings, "feature_gmail_pubsub_enabled", True),
            patch.object(settings, "gmail_pubsub_project_id", "codejob-prod"),
            patch.object(settings, "gmail_pubsub_topic_id", "gmail-mailbox-events"),
            patch.object(settings, "gmail_pubsub_subscription_id", "codejob-sub"),
        ]
        for item in self._patches:
            item.start()
        self.seed()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def seed(self):
        self.db.add(User(owner_id=MINE, email=MY_ADDRESS, display_name="Me"))
        self.db.add(UserSettings(owner_id=MINE, signature_email=MY_ADDRESS,
                                 feature_reply_inbox_enabled=True))
        self.db.add(GmailCredential(owner_id=MINE, google_email=MY_ADDRESS,
                                    access_token_encrypted="cipher",
                                    refresh_token_encrypted="cipher",
                                    gmail_history_id="100"))
        self.db.add(User(owner_id=THEIRS, email="them@example.com", display_name="Them"))
        self.db.add(UserSettings(owner_id=THEIRS, signature_email="them@example.com",
                                 feature_reply_inbox_enabled=True))
        self.db.add(GmailCredential(owner_id=THEIRS, google_email="them@example.com",
                                    access_token_encrypted="cipher",
                                    refresh_token_encrypted="cipher",
                                    gmail_history_id="100"))
        self.db.commit()

    def deliver(self, address=MY_ADDRESS, history_id="4821"):
        message = _Message({"emailAddress": address, "historyId": history_id})
        subscriber.handle_message(message)
        return message

    def credential(self, owner_id=MINE) -> GmailCredential:
        self.db.expire_all()
        return self.db.query(GmailCredential).filter(
            GmailCredential.owner_id == owner_id).one()


class RoutingTests(_Base):
    def test_a_notification_enqueues_a_drain_for_that_owner(self):
        message = self.deliver()

        self.assertTrue(message.acked)
        self.assertEqual(len(self.queue.jobs), 1)
        func, kwargs = self.queue.jobs[0]
        self.assertEqual(func, "app.jobs.tasks.run_gmail_history_job")
        self.assertEqual(kwargs["owner_id"], MINE)
        self.assertEqual(kwargs["notification_history_id"], "4821")

    def test_the_owner_comes_from_the_credential_table_not_the_payload(self):
        """The single most important line in this file. An owner id that could
        arrive in a notification would be a way to write into any account."""
        message = _Message({
            "emailAddress": MY_ADDRESS, "historyId": "1", "owner_id": THEIRS,
        })

        subscriber.handle_message(message)

        self.assertEqual(self.queue.jobs[0][1]["owner_id"], MINE)

    def test_an_address_is_matched_case_insensitively(self):
        self.deliver(address="ME@EXAMPLE.COM")

        self.assertEqual(self.queue.jobs[0][1]["owner_id"], MINE)

    def test_each_mailbox_routes_to_its_own_owner(self):
        self.deliver(address=MY_ADDRESS)
        self.deliver(address="them@example.com")

        self.assertEqual(
            [job[1]["owner_id"] for job in self.queue.jobs], [MINE, THEIRS]
        )

    def test_the_arrival_is_recorded_without_the_payload(self):
        self.deliver()

        row = self.credential()
        self.assertIsNotNone(row.gmail_last_notification_at)
        self.assertGreater(row.gmail_last_notification_at, datetime.now(UTC) - timedelta(minutes=1))

    def test_the_stored_cursor_is_never_moved_by_a_notification(self):
        """The subscriber records that one arrived and nothing else. Moving the
        cursor here would skip everything between it and the last drain."""
        self.deliver(history_id="999999")

        self.assertEqual(self.credential().gmail_history_id, "100")


class AcknowledgementTests(_Base):
    def test_a_malformed_message_is_acknowledged_and_dropped(self):
        message = _Message(b"not json")

        subscriber.handle_message(message)

        self.assertTrue(message.acked)
        self.assertFalse(message.nacked)
        self.assertEqual(self.queue.jobs, [])

    def test_an_unknown_mailbox_is_acknowledged(self):
        """Redelivering it forever cannot make it known here, and a backlog of
        poison is what eventually delays the valid ones."""
        message = self.deliver(address="stranger@example.com")

        self.assertTrue(message.acked)
        self.assertEqual(self.queue.jobs, [])

    def test_a_revoked_credential_is_acknowledged_without_work(self):
        row = self.credential()
        row.revoked_at = datetime.now(UTC)
        self.db.commit()

        message = self.deliver()

        self.assertTrue(message.acked)
        self.assertEqual(self.queue.jobs, [])

    def test_a_disabled_owner_is_acknowledged_without_work(self):
        user = self.db.query(User).filter(User.owner_id == MINE).one()
        user.disabled_at = datetime.now(UTC)
        self.db.commit()

        message = self.deliver()

        self.assertTrue(message.acked)
        self.assertEqual(self.queue.jobs, [])

    def test_a_switched_off_reply_inbox_is_acknowledged_without_work(self):
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == MINE).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        message = self.deliver()

        self.assertTrue(message.acked)
        self.assertEqual(self.queue.jobs, [])

    def test_redis_being_down_defers_rather_than_drops(self):
        """The one case redelivery helps. Acknowledging here would be a reply
        that never arrives, with nothing anywhere saying so."""
        def explode(name):
            raise ConnectionError("redis is down")

        with patch.object(subscriber, "get_queue", explode):
            message = self.deliver()

        self.assertTrue(message.nacked)
        self.assertFalse(message.acked)

    def test_the_message_is_acknowledged_only_after_the_enqueue(self):
        order = []

        class Recording(_Message):
            def ack(self):
                order.append("ack")
                super().ack()

        def enqueue_then_record(name):
            order.append("enqueue")
            return self.queue

        with patch.object(subscriber, "get_queue", enqueue_then_record):
            message = Recording({"emailAddress": MY_ADDRESS, "historyId": "1"})
            subscriber.handle_message(message)

        self.assertEqual(order, ["enqueue", "ack"])

    def test_nothing_sensitive_reaches_the_log(self):
        """An `emailAddress` is the one piece of personal data this process
        handles, and the payload is the other."""
        with self.assertLogs(subscriber.logger, level="INFO") as captured:
            self.deliver(address="stranger@example.com")
            subscriber.handle_message(_Message(b"not json"))

        blob = "\n".join(captured.output)
        self.assertNotIn("stranger@example.com", blob)
        self.assertNotIn("historyId", blob)


class SweepTests(_Base):
    def test_the_sweep_registers_what_is_due_and_stops_what_is_not(self):
        # Mine is eligible and has never renewed; theirs has the Reply Inbox
        # switched off while still holding watch state.
        row = self.db.query(UserSettings).filter(UserSettings.owner_id == THEIRS).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        with patch.object(gmail_pubsub_service, "register_or_renew_watch",
                          return_value=gmail_pubsub_service.WatchOutcome(True)) as register, \
                patch.object(gmail_pubsub_service, "stop_watch") as stop, \
                patch.object(distributed_lock, "hold", self._lock):
            registered, stopped = subscriber.sweep_watches()

        self.assertEqual((registered, stopped), (1, 1))
        register.assert_called_once_with(MINE)
        stop.assert_called_once_with(THEIRS)

    def test_a_second_subscriber_does_not_double_register(self):
        """Pub/Sub load-balances a subscription across every subscriber, so a
        second copy of this service is a supported accident."""
        @contextmanager
        def busy(name, *, owner_id, **kwargs):
            raise distributed_lock.LockBusy(name)
            yield

        with patch.object(distributed_lock, "hold", busy), \
                patch.object(gmail_pubsub_service, "register_or_renew_watch") as register:
            self.assertEqual(subscriber.sweep_watches(), (0, 0))

        register.assert_not_called()

    def test_the_lease_is_global_rather_than_per_owner(self):
        held = []

        @contextmanager
        def record(name, *, owner_id, **kwargs):
            held.append((name, owner_id))
            yield

        with patch.object(distributed_lock, "hold", record), \
                patch.object(gmail_pubsub_service, "register_or_renew_watch",
                             return_value=gmail_pubsub_service.WatchOutcome(False)):
            subscriber.sweep_watches()

        self.assertEqual(held, [(subscriber.WATCH_LEADER_LOCK, subscriber.LEADER_SCOPE)])

    @staticmethod
    @contextmanager
    def _lock(name, *, owner_id, **kwargs):
        yield


class HeartbeatTests(unittest.TestCase):
    class _Redis:
        def __init__(self):
            self.keys = {}

        def set(self, key, value, ex=None):
            self.keys[key] = (value, ex)

        def exists(self, key):
            return key in self.keys

    def test_the_heartbeat_expires_on_its_own(self):
        """Its whole purpose. A key without a TTL would keep saying the
        subscriber is online long after the container stopped."""
        redis = self._Redis()

        subscriber.write_heartbeat(redis)

        _, ttl = redis.keys[subscriber.HEARTBEAT_KEY]
        self.assertEqual(ttl, subscriber.HEARTBEAT_TTL_SECONDS)
        self.assertGreater(ttl, subscriber.HEARTBEAT_INTERVAL_SECONDS)

    def test_online_means_a_live_heartbeat(self):
        redis = self._Redis()

        self.assertFalse(subscriber.subscriber_is_online(redis))
        subscriber.write_heartbeat(redis)
        self.assertTrue(subscriber.subscriber_is_online(redis))

    def test_redis_being_unreachable_is_not_evidence_of_life(self):
        class Broken:
            def exists(self, key):
                raise ConnectionError("down")

        self.assertFalse(subscriber.subscriber_is_online(Broken()))


class StartupTests(unittest.TestCase):
    def test_it_exits_quietly_when_the_feature_is_off(self):
        with patch.object(settings, "feature_gmail_pubsub_enabled", False):
            self.assertEqual(subscriber.run(), 0)

    def test_it_exits_loudly_when_the_subscription_is_unnamed(self):
        """A subscriber that starts and consumes nothing is indistinguishable
        from a quiet mailbox."""
        with patch.object(settings, "feature_gmail_pubsub_enabled", True), \
                patch.object(settings, "gmail_pubsub_subscription_id", ""):
            self.assertEqual(subscriber.run(), 2)


if __name__ == "__main__":
    unittest.main()


class ComposeTests(unittest.TestCase):
    """The subscriber only helps if something actually runs it.

    Parsed as YAML rather than searched as text. An earlier version of this
    pattern elsewhere in the repo matched a *comment* mentioning the flag it
    was checking for, and stayed green after the flag itself was deleted from
    the command.
    """

    @classmethod
    def setUpClass(cls):
        import yaml
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        cls.compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
        cls.service = cls.compose["services"]["gmail-pubsub"]

    def test_a_service_runs_the_subscriber_module(self):
        self.assertIn("app.gmail_pubsub_subscriber", self.service["command"])

    def test_the_worker_consumes_the_event_queue(self):
        """A queue nothing consumes accepts jobs silently and runs none."""
        command = self.compose["services"]["worker"]["command"]

        self.assertIn(GMAIL_EVENT_QUEUE, command.split())

    def test_the_credential_is_mounted_read_only_and_not_built_in(self):
        """A service-account key inside the image would ship in every layer."""
        mounts = self.service["volumes"]

        self.assertTrue(any(mount.endswith(":ro") for mount in mounts), mounts)
        self.assertTrue(any("/run/secrets" in mount for mount in mounts), mounts)

    def test_the_subscriber_publishes_no_port(self):
        """Pull, not push. Nothing publicly reachable exists for Google to
        deliver to, which is the reason this architecture was chosen."""
        self.assertNotIn("ports", self.service)

    def test_it_restarts_unless_stopped(self):
        self.assertEqual(self.service.get("restart"), "unless-stopped")


class RetryTests(_Base):
    def test_a_drain_is_retried_in_the_worker_not_by_redelivery(self):
        """A transient Gmail 429 must not hold a Pub/Sub message open for
        minutes. Safe to repeat: the drain starts from the committed cursor,
        which a failure leaves untouched."""
        self.deliver()

        retry = self.queue.jobs[0][1]["retry"]
        self.assertEqual(retry.max, 3)
        self.assertGreaterEqual(sum(retry.intervals), 300,
                                "the backoff has to outlast a rate limit, not argue with one")

    def test_the_job_has_a_timeout(self):
        """RQ's default is 180s. A mailbox with a long backlog drains for
        longer than that, and a killed drain leaves the cursor where it was
        and starts over - forever."""
        self.deliver()

        self.assertGreater(self.queue.jobs[0][1]["job_timeout"], 180)

    def test_no_job_id_is_derived_from_the_notification(self):
        """Several notifications legitimately coalesce into one history range.
        Deduplicating on the number would drop drains that were needed."""
        self.deliver()

        self.assertNotIn("job_id", self.queue.jobs[0][1])


class StreamingTests(unittest.TestCase):
    """The one test that drives `run()` end to end, with a fake client.

    It doubles as proof that the Google import is lazy: this module was
    imported at the top of the file, and `google.cloud.pubsub_v1` is not
    installed in the test environment. If the import moved to module scope,
    every test in this file would fail to collect.
    """

    def _fake_pubsub(self, *, future):
        import sys
        import types

        recorded = {}

        class SubscriberClient:
            def subscribe(self, subscription, callback, flow_control=None):
                recorded["subscription"] = subscription
                recorded["callback"] = callback
                recorded["flow_control"] = flow_control
                return future

            def close(self):
                recorded["closed"] = True

        module = types.ModuleType("google.cloud.pubsub_v1")
        module.SubscriberClient = SubscriberClient
        module.types = types.SimpleNamespace(
            FlowControl=lambda **kwargs: types.SimpleNamespace(**kwargs)
        )
        cloud = sys.modules.get("google.cloud") or types.ModuleType("google.cloud")
        return module, cloud, recorded

    def test_it_subscribes_to_the_configured_subscription_and_shuts_down(self):
        import sys
        from unittest.mock import MagicMock

        future = MagicMock()
        future.result.return_value = None
        module, cloud, recorded = self._fake_pubsub(future=future)

        with patch.dict(sys.modules, {"google.cloud.pubsub_v1": module, "google.cloud": cloud}), \
                patch.object(settings, "feature_gmail_pubsub_enabled", True), \
                patch.object(settings, "gmail_pubsub_project_id", "codejob-prod"), \
                patch.object(settings, "gmail_pubsub_subscription_id", "codejob-sub"), \
                patch.object(settings, "gmail_pubsub_topic_id", "gmail-mailbox-events"), \
                patch.object(subscriber, "_background_loop", lambda stop: None):
            setattr(cloud, "pubsub_v1", module)
            code = subscriber.run()

        self.assertEqual(code, 0)
        self.assertEqual(
            recorded["subscription"], "projects/codejob-prod/subscriptions/codejob-sub"
        )
        self.assertIs(recorded["callback"], subscriber.handle_message)
        self.assertEqual(
            recorded["flow_control"].max_messages, subscriber.MAX_OUTSTANDING_MESSAGES
        )
        self.assertTrue(recorded["closed"], "the client is closed on the way out")

    def test_a_broken_stream_exits_non_zero_so_docker_restarts_it(self):
        import sys
        from unittest.mock import MagicMock

        future = MagicMock()
        future.result.side_effect = RuntimeError("stream died")
        module, cloud, _ = self._fake_pubsub(future=future)

        with patch.dict(sys.modules, {"google.cloud.pubsub_v1": module, "google.cloud": cloud}), \
                patch.object(settings, "feature_gmail_pubsub_enabled", True), \
                patch.object(settings, "gmail_pubsub_project_id", "codejob-prod"), \
                patch.object(settings, "gmail_pubsub_subscription_id", "codejob-sub"), \
                patch.object(subscriber, "_background_loop", lambda stop: None):
            setattr(cloud, "pubsub_v1", module)
            self.assertEqual(subscriber.run(), 1)
