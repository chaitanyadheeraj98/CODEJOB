"""What Settings is allowed to say about push delivery, and what it must not.

Five words, ordered by what the person reading them can do about it. A
configuration error outranks a lapsed watch because the watch cannot be
registered until the configuration is fixed. A missing consumer outranks a
healthy watch because a watch nobody listens to delivers nothing - and that is
the failure a stored watch looks exactly like.

The second half of this file is the one that matters more: the response carries
no topic name, no subscription path, no service-account detail and no mailbox
history id.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database
from app import gmail_pubsub_subscriber, main
from app.config import settings
from app.db import Base
from app.models import GmailCredential, User, UserSettings
from app.services import gmail_credential_service, gmail_pubsub_service as service

OWNER = "default-owner"
ADDRESS = "me@example.com"
CURSOR = "556677"
TOPIC = "gmail-mailbox-events"
PROJECT = "codejob-prod"


def _status(**overrides):
    base = dict(
        owner_id=OWNER, connected=True, google_email=ADDRESS,
        watch_active=True, watch_expires_at=datetime.now(UTC) + timedelta(days=6),
        has_history_cursor=True, watch_error="",
        last_notification_at=None, last_event_processed_at=None,
    )
    base.update(overrides)
    return gmail_credential_service.GmailConnectionStatus(**base)


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self._patches = [
            patch.object(settings, "feature_gmail_pubsub_enabled", True),
            patch.object(settings, "gmail_pubsub_project_id", PROJECT),
            patch.object(settings, "gmail_pubsub_topic_id", TOPIC),
        ]
        for item in self._patches:
            item.start()

    def tearDown(self):
        for item in self._patches:
            item.stop()

    def state(self, status, *, online=True, eligible=True):
        return service.delivery_state(status, consumer_online=online, eligible=eligible)

    def test_a_watched_mailbox_with_a_live_consumer_is_active(self):
        self.assertEqual(self.state(_status()), service.DELIVERY_ACTIVE)

    def test_no_watch_yet_is_registering(self):
        self.assertEqual(
            self.state(_status(watch_active=False, has_history_cursor=False)),
            service.DELIVERY_REGISTERING,
        )

    def test_a_lapsed_watch_also_reads_as_registering(self):
        """"Expired" is a distinction the person reading it cannot act on. The
        minute loop registers a new one either way."""
        self.assertEqual(
            self.state(_status(watch_active=False)), service.DELIVERY_REGISTERING
        )

    def test_a_watch_error_outranks_everything_below_it(self):
        """The watch cannot be registered until the configuration is fixed, so
        saying "starting" would be a lie that repeats every fifteen seconds."""
        self.assertEqual(
            self.state(_status(watch_active=False, watch_error="watch_topic_not_found")),
            service.DELIVERY_ERROR,
        )

    def test_a_silent_consumer_is_delayed_even_with_a_healthy_watch(self):
        """The failure a stored watch looks exactly like."""
        self.assertEqual(self.state(_status(), online=False), service.DELIVERY_DELAYED)

    def test_an_unprocessed_notification_is_delayed(self):
        stale = datetime.now(UTC) - timedelta(minutes=30)
        self.assertEqual(
            self.state(_status(last_notification_at=stale, last_event_processed_at=None)),
            service.DELIVERY_DELAYED,
        )

    def test_a_notification_processed_after_it_arrived_is_active(self):
        arrived = datetime.now(UTC) - timedelta(minutes=30)
        self.assertEqual(
            self.state(_status(
                last_notification_at=arrived,
                last_event_processed_at=arrived + timedelta(seconds=4),
            )),
            service.DELIVERY_ACTIVE,
        )

    def test_a_notification_from_a_moment_ago_is_not_yet_delayed(self):
        """A drain takes seconds. Flagging one in flight would put the status
        line into a flicker nobody can act on."""
        self.assertEqual(
            self.state(_status(last_notification_at=datetime.now(UTC))),
            service.DELIVERY_ACTIVE,
        )

    def test_the_delay_threshold_is_minutes_not_hours(self):
        self.assertLessEqual(service.DELAYED_AFTER, timedelta(minutes=15))
        self.assertGreaterEqual(service.DELAYED_AFTER, timedelta(minutes=1))

    def test_a_switched_off_reply_inbox_reads_as_disabled(self):
        self.assertEqual(self.state(_status(), eligible=False), service.DELIVERY_DISABLED)

    def test_a_disconnected_mailbox_reads_as_disabled(self):
        self.assertEqual(
            self.state(_status(connected=False)), service.DELIVERY_DISABLED
        )

    def test_push_being_off_reads_as_disabled_whatever_the_row_says(self):
        with patch.object(settings, "feature_gmail_pubsub_enabled", False):
            self.assertEqual(self.state(_status()), service.DELIVERY_DISABLED)


class ResponseTests(unittest.TestCase):
    """The endpoint, and what it refuses to carry."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.db = self.Session()
        self.db.add(User(owner_id=OWNER, email=ADDRESS, display_name="Me"))
        self.db.add(UserSettings(owner_id=OWNER, signature_email=ADDRESS,
                                 feature_reply_inbox_enabled=True))
        self.db.add(GmailCredential(
            owner_id=OWNER, google_email=ADDRESS,
            access_token_encrypted="cipher", refresh_token_encrypted="cipher",
            gmail_history_id=CURSOR,
            gmail_watch_expiration_at=datetime.now(UTC) + timedelta(days=6),
            gmail_watch_renewed_at=datetime.now(UTC),
        ))
        self.db.commit()
        main.app.dependency_overrides[main.get_db] = self._session
        self.client = TestClient(main.app)
        self._patches = [
            patch.object(settings, "feature_gmail_pubsub_enabled", True),
            patch.object(settings, "gmail_pubsub_project_id", PROJECT),
            patch.object(settings, "gmail_pubsub_topic_id", TOPIC),
            patch.object(settings, "feature_db_credentials_enabled", True),
            patch.object(gmail_pubsub_subscriber, "subscriber_is_online", lambda: True),
            # `gmail_connection_state` opens its own session rather than taking
            # the request's, so overriding `get_db` alone leaves it reading a
            # database with no tables in it.
            patch.object(database, "SessionLocal", self.Session),
        ]
        for item in self._patches:
            item.start()

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        for item in self._patches:
            item.stop()
        main.app.dependency_overrides.pop(main.get_db, None)
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def body(self):
        response = self.client.get("/gmail/connection")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_it_reports_delivery_as_active(self):
        body = self.body()

        self.assertEqual(body["inbox_delivery"], "active")
        self.assertTrue(body["consumer_online"])
        self.assertIsNotNone(body["watch_expires_at"])

    def test_the_history_cursor_never_reaches_the_browser(self):
        """A position in someone's mailbox, of no use to a page that only has
        to say whether mail is arriving."""
        raw = self.client.get("/gmail/connection").text

        self.assertNotIn(CURSOR, raw)
        self.assertNotIn("history", raw.lower())

    def test_no_cloud_resource_name_reaches_the_browser(self):
        """These describe the deployment, not the account. A topic name in a
        response body is a detail about somebody else's infrastructure."""
        raw = self.client.get("/gmail/connection").text

        for leaked in (TOPIC, PROJECT, "projects/", "subscriptions/", "/run/secrets"):
            self.assertNotIn(leaked, raw, f"{leaked} must not reach the browser")

    def test_no_token_material_reaches_the_browser(self):
        raw = self.client.get("/gmail/connection").text

        self.assertNotIn("cipher", raw)
        self.assertNotIn("refresh_token", raw)

    def test_a_silent_subscriber_shows_as_delayed(self):
        with patch.object(gmail_pubsub_subscriber, "subscriber_is_online", lambda: False):
            body = self.body()

        self.assertEqual(body["inbox_delivery"], "delayed")
        self.assertFalse(body["consumer_online"])

    def test_the_watch_error_is_a_code_rather_than_a_sentence(self):
        row = self.db.query(GmailCredential).one()
        row.gmail_watch_last_error = "watch_permission_denied"
        self.db.commit()

        body = self.body()

        self.assertEqual(body["inbox_delivery"], "error")
        self.assertEqual(body["watch_error"], "watch_permission_denied")

    def test_delivery_is_disabled_while_the_reply_inbox_is_off(self):
        row = self.db.query(UserSettings).one()
        row.feature_reply_inbox_enabled = False
        self.db.commit()

        self.assertEqual(self.body()["inbox_delivery"], "disabled")


if __name__ == "__main__":
    unittest.main()
