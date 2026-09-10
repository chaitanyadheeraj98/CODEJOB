"""Wiring for the Labels workspace routes.

The service is tested directly in test_label_dossier_service.py; this covers
what only a request can reach - the feature gate, the 404 shape, and that
marking read clears every conversation in the dossier rather than one.
"""

import json
import os
import unittest
from datetime import UTC, datetime, timedelta

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    GmailLabel,
    RecruiterWatch,
    TrackedThread,
    UserSettings,
)

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


class LabelDossierApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.owner = main.settings.owner_id
        with Session(self.engine) as db:
            db.add(UserSettings(owner_id=self.owner, feature_label_tracking_enabled=True, signature_email="me@gmail.com"))
            db.add(GmailLabel(owner_id=self.owner, external_label_id="Label_1", name="RTR Requested", is_tracked=True))
            labeled = EmailConversation(owner_id=self.owner, external_thread_id="thread-1", origin="label",
                subject_snapshot="RTR", recruiter_email_snapshot="naman@valzosoft.com", last_message_at=NOW,
                unread_reply_count=1)
            follow_up = EmailConversation(owner_id=self.owner, external_thread_id="thread-2", origin="watch",
                subject_snapshot="Follow up", recruiter_email_snapshot="priya@valzosoft.com",
                last_message_at=NOW + timedelta(hours=2), unread_reply_count=1)
            db.add_all([labeled, follow_up])
            db.flush()
            watch = RecruiterWatch(owner_id=self.owner, watch_type="domain", value="valzosoft.com",
                source_thread_ids_json=json.dumps(["thread-1"]), origin_label_external_id="Label_1")
            db.add(watch)
            db.flush()
            db.add(TrackedThread(owner_id=self.owner, external_thread_id="thread-1", conversation_id=labeled.id,
                label_external_ids_json=json.dumps(["Label_1"]), subject_snapshot="RTR", last_message_at=NOW,
                participants_json=json.dumps([{"address": "naman@valzosoft.com", "role": "from"}])))
            db.add_all([
                EmailReplyMessage(owner_id=self.owner, conversation_id=labeled.id, direction="inbound",
                    external_message_id="m1", sender="Naman <naman@valzosoft.com>", to_header="me@gmail.com",
                    snippet="RTR attached", body="RTR attached", received_at=NOW),
                EmailReplyMessage(owner_id=self.owner, conversation_id=follow_up.id, direction="inbound",
                    external_message_id="m3", sender="Priya <priya@valzosoft.com>", to_header="me@gmail.com",
                    snippet="Taking over", body="Taking over", received_at=NOW + timedelta(hours=2),
                    matched_watch_id=watch.id),
            ])
            db.commit()

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        self.engine.dispose()

    def _disable_feature(self) -> None:
        with Session(self.engine) as db:
            db.query(UserSettings).filter(UserSettings.owner_id == self.owner).one().feature_label_tracking_enabled = False
            db.commit()

    def test_overview_lists_tracked_labels_with_counts(self) -> None:
        body = self.client.get("/labels/overview").json()

        self.assertEqual([(i["name"], i["thread_count"]) for i in body["items"]], [("RTR Requested", 1)])
        self.assertEqual(body["tracked_thread_total"], 1)

    def test_dossier_unions_the_labeled_thread_with_its_watch_matches(self) -> None:
        body = self.client.get("/labels/threads/thread-1/dossier").json()

        self.assertEqual([m["snippet"] for m in body["messages"]], ["RTR attached", "Taking over"])
        self.assertEqual(body["thread_count"], 2)
        self.assertEqual(body["unread_count"], 2)

    def test_a_thread_id_that_is_not_tracked_is_404_not_500(self) -> None:
        response = self.client.get("/labels/threads/nope/dossier")

        self.assertEqual(response.status_code, 404)
        self.assertIn("tracked label", response.json()["detail"])

    def test_marking_read_clears_every_conversation_in_the_dossier(self) -> None:
        body = self.client.post("/labels/threads/thread-1/read").json()

        self.assertEqual(body["unread_count"], 0)
        with Session(self.engine) as db:
            counts = {c.external_thread_id: c.unread_reply_count for c in db.query(EmailConversation)}
        self.assertEqual(counts, {"thread-1": 0, "thread-2": 0})

    def test_every_route_is_hidden_when_the_feature_is_off(self) -> None:
        self._disable_feature()

        for method, path in (("get", "/labels/overview"), ("get", "/labels/threads/thread-1/dossier"),
                             ("post", "/labels/threads/thread-1/read")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)


if __name__ == "__main__":
    unittest.main()
