import os
import unittest
from datetime import UTC, datetime

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.gmail_client import _append_tracking_pixel
from app.models import EmailConversation, EmailOpenEvent, EmailReplyMessage, RecruiterEmail, UserSettings
from app.parsing.document_extraction import extract_gmail_reply_body
from app.services.email_inbox_service import ensure_sent_conversation, generate_tracking_token, list_conversations, tracking_pixel_url


class EmailTrackingInboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        main.orchestration_service = None
        self.client = TestClient(main.app)

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        main.orchestration_service = None
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _add_settings(self, db: Session, *, reply_inbox: bool = False) -> UserSettings:
        row = UserSettings(
            owner_id=main.settings.owner_id,
            enabled=True,
            gmail_query="is:unread in:inbox recruiter",
            default_gmail_query="is:unread in:inbox recruiter",
            default_date_mode="off",
            feature_reply_inbox_enabled=reply_inbox,
            feature_email_tracking_enabled=False,
            signature_email="me@example.com",
            policy_json="",
        )
        db.add(row)
        db.flush()
        return row

    def _add_sent_email(
        self,
        db: Session,
        *,
        token: str = "tracking-token",
        external_message_id: str = "source-message",
        external_thread_id: str = "thread-123",
        gmail_sent_id: str = "sent-message",
    ) -> RecruiterEmail:
        row = RecruiterEmail(
            owner_id=main.settings.owner_id,
            sender="Recruiter <recruiter@example.com>",
            subject="Java role",
            body="Need Java",
            role="Java Developer",
            location="Remote",
            draft_reply="Hello recruiter",
            state="approved_sent",
            decision="Qualified",
            approval_status="approved",
            sent_status="sent",
            source="gmail",
            external_message_id=external_message_id,
            external_thread_id=external_thread_id,
            recipient_email="recruiter@example.com",
            cc_email="manager@example.com",
            sent_at=datetime.now(UTC),
            gmail_sent_id=gmail_sent_id,
            tracking_token=token,
        )
        db.add(row)
        db.flush()
        return row

    def test_tracking_token_and_public_url_are_safe_by_default(self) -> None:
        token = generate_tracking_token("secret", 42)
        self.assertEqual(len(token), 22)
        with Session(self.engine) as db:
            email = self._add_sent_email(db)
            conversation = ensure_sent_conversation(db, owner_id=main.settings.owner_id, root_email=email, thread_id="thread-123")
            db.flush()
            summary = list_conversations(db, main.settings.owner_id)[0]
            self.assertEqual(summary.root_recruiter_email_id, email.id)
            self.assertEqual(summary.origin, "sent")
            self.assertEqual(summary.labels, [])
            self.assertEqual(summary.subject, email.subject)
        self.assertIsNone(tracking_pixel_url("http://localhost:8000", token))
        self.assertEqual(
            tracking_pixel_url("https://mail.example.com/", token),
            f"https://mail.example.com/track/open/{token}.png",
        )
        html = _append_tracking_pixel("<div>Hello</div>", "https://mail.example.com/track/open/token.png")
        self.assertIn('<img src="https://mail.example.com/track/open/token.png"', html)
        self.assertEqual(
            extract_gmail_reply_body("Can you talk Tuesday?\n\nOn Tue, Aug 5, 2026 at 1:00 PM Me wrote:\n> Old text"),
            "Can you talk Tuesday?",
        )

    def test_pixel_and_inbox_api_record_open_read_and_outbound_reply(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db)
            email = self._add_sent_email(db)
            conversation = ensure_sent_conversation(
                db,
                owner_id=main.settings.owner_id,
                root_email=email,
                thread_id="thread-123",
            )
            db.commit()
            email_id = email.id
            conversation_id = conversation.id

        pixel = self.client.get(
            "/track/open/tracking-token.png",
            headers={"User-Agent": "Mozilla/5.0 GoogleImageProxy"},
        )
        self.assertEqual(pixel.status_code, 200)
        self.assertEqual(pixel.headers["content-type"], "image/png")
        self.assertEqual(pixel.headers["cache-control"], "no-store, max-age=0")

        with Session(self.engine) as db:
            email = db.get(RecruiterEmail, email_id)
            conversation = db.get(EmailConversation, conversation_id)
            assert email is not None and conversation is not None
            self.assertEqual(email.open_count, 1)
            self.assertIsNotNone(email.opened_at)
            self.assertEqual(conversation.status, "opened")
            event = db.query(EmailOpenEvent).one()
            self.assertTrue(event.is_likely_proxy)
            db.add(
                EmailReplyMessage(
                    owner_id=main.settings.owner_id,
                    conversation_id=conversation_id,
                    direction="inbound",
                    external_message_id="reply-1",
                    sender="Recruiter <recruiter@example.com>",
                    body="Can you talk Tuesday?",
                    snippet="Can you talk Tuesday?",
                    received_at=datetime.now(UTC),
                )
            )
            conversation.status = "replied"
            conversation.unread_reply_count = 1
            db.commit()

        listing = self.client.get("/inbox/conversations")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual(listing.json()[0]["unread_reply_count"], 1)

        read = self.client.post(f"/inbox/conversations/{conversation_id}/read")
        self.assertEqual(read.status_code, 200, read.text)
        self.assertEqual(read.json()["unread_reply_count"], 0)

        original_send = main.send_reply_with_attachment
        sent_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        try:
            main.send_reply_with_attachment = lambda *args, **kwargs: (sent_calls.append((args, kwargs)), "reply-sent")[1]
            reply = self.client.post(
                f"/inbox/conversations/{conversation_id}/reply",
                json={"body": "Tuesday works for me."},
            )
        finally:
            main.send_reply_with_attachment = original_send
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual(reply.json()["messages"][-1]["direction"], "outbound")
        self.assertEqual(sent_calls[0][0][0], "thread-123")

    def test_sync_short_circuits_known_thread_reply_before_jd_classification(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db, reply_inbox=True)
            sent_email = self._add_sent_email(db, token="tracking-token-2")
            ensure_sent_conversation(
                db,
                owner_id=main.settings.owner_id,
                root_email=sent_email,
                thread_id="thread-123",
                sent_rfc_message_id="<sent@example.com>",
            )
            db.commit()

        reply_item = {
            "external_message_id": "incoming-reply",
            "external_thread_id": "thread-123",
            "external_rfc_message_id": "<reply@example.com>",
            "in_reply_to_header": "<sent@example.com>",
            "references_header": "<source@example.com> <sent@example.com>",
            "sender": "Recruiter <recruiter@example.com>",
            "recipient_email": "recruiter@example.com",
            "subject": "Re: Java role",
            "body": "Can you talk Tuesday?\n\nOn Tue, Aug 5, 2026 at 1:00 PM Me wrote:\n> Old text",
            "snippet": "Can you talk Tuesday?",
            "gmail_received_at": datetime.now(UTC),
            "label_ids": ["INBOX", "UNREAD"],
            "to_header": "me@example.com",
            "cc_header": "",
            "list_id": "",
            "list_post": "",
            "list_unsubscribe": "",
            "delivered_to": "me@example.com",
            "mailing_list": "",
        }
        fallback_reply_item = {
            **reply_item,
            "external_message_id": "incoming-fallback-reply",
            "external_thread_id": "fresh-thread-456",
            "external_rfc_message_id": "<fresh-reply@example.com>",
            "body": "Wednesday also works.\n\nOn Tue, Aug 5, 2026 at 2:00 PM Me wrote:\n> Earlier text",
            "snippet": "Wednesday also works.",
        }
        originals = (
            main.is_gmail_configured,
            main.list_unread_candidates_by_query,
            main.classify_email_intent,
            main.mark_reply_processed,
            main.list_thread_messages,
        )
        marked: list[str] = []
        try:
            main.is_gmail_configured = lambda: True
            main.list_unread_candidates_by_query = lambda query, **_kwargs: (
                [reply_item, fallback_reply_item] if query == "is:unread in:inbox" else []
            )
            main.classify_email_intent = lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("reply reached JD intent classification")
            )
            main.mark_reply_processed = lambda message_id, _labels=None: marked.append(message_id)
            # Reply is matched via the unread-search + in_reply_to_header path above, not the
            # per-thread rescan - this stubs that second scan path out so it doesn't reach Gmail.
            main.list_thread_messages = lambda thread_id: []
            main.orchestration_service = None
            response = self.client.post("/gmail/sync")
        finally:
            (
                main.is_gmail_configured,
                main.list_unread_candidates_by_query,
                main.classify_email_intent,
                main.mark_reply_processed,
                main.list_thread_messages,
            ) = originals
            main.orchestration_service = None

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported_count"], 2)
        self.assertEqual(marked, ["incoming-reply", "incoming-fallback-reply"])
        with Session(self.engine) as db:
            self.assertIsNone(
                db.query(RecruiterEmail).filter(RecruiterEmail.external_message_id == "incoming-reply").first()
            )
            messages = (
                db.query(EmailReplyMessage)
                .filter(EmailReplyMessage.direction == "inbound")
                .order_by(EmailReplyMessage.id.asc())
                .all()
            )
            self.assertEqual(len(messages), 2)
            message = messages[0]
            self.assertEqual(message.body, "Can you talk Tuesday?")
            conversation = db.get(EmailConversation, message.conversation_id)
            assert conversation is not None
            self.assertEqual(conversation.status, "replied")
            self.assertEqual(conversation.unread_reply_count, 2)
            self.assertEqual(conversation.external_thread_id, "fresh-thread-456")
            self.assertEqual(messages[1].body, "Wednesday also works.")

    def test_capture_inbound_reply_ignores_self_sent_thread_messages(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db, reply_inbox=True)
            sent_email = self._add_sent_email(db, token="tracking-token-3", gmail_sent_id="legacy-mismatched-id")
            ensure_sent_conversation(
                db,
                owner_id=main.settings.owner_id,
                root_email=sent_email,
                thread_id="thread-123",
                sent_rfc_message_id="<sent@example.com>",
            )
            db.commit()

        # Deliberately a different id than gmail_sent_id above, simulating a record whose
        # real Gmail id was never captured at send time - the id-based dedup alone can't
        # catch this; the sender check must.
        self_sent_item = {
            "external_message_id": "actual-gmail-sent-id",
            "external_thread_id": "thread-123",
            "external_rfc_message_id": "<sent@example.com>",
            "in_reply_to_header": "",
            "references_header": "",
            "sender": "Me <me@example.com>",
            "recipient_email": "recruiter@example.com",
            "subject": "Java role",
            "body": "Hello recruiter",
            "snippet": "Hello recruiter",
            "gmail_received_at": datetime.now(UTC),
            "label_ids": ["SENT"],
            "to_header": "recruiter@example.com",
            "cc_header": "",
            "list_id": "",
            "list_post": "",
            "list_unsubscribe": "",
            "delivered_to": "",
            "mailing_list": "",
        }
        originals = (
            main.is_gmail_configured,
            main.list_unread_candidates_by_query,
            main.list_thread_messages,
            main.mark_reply_processed,
        )
        try:
            main.is_gmail_configured = lambda: True
            main.list_unread_candidates_by_query = lambda query, **_kwargs: []
            main.list_thread_messages = lambda thread_id: [self_sent_item] if thread_id == "thread-123" else []
            main.mark_reply_processed = lambda message_id, _labels=None: None
            main.orchestration_service = None
            response = self.client.post("/inbox/conversations/refresh")
        finally:
            (
                main.is_gmail_configured,
                main.list_unread_candidates_by_query,
                main.list_thread_messages,
                main.mark_reply_processed,
            ) = originals
            main.orchestration_service = None

        self.assertEqual(response.status_code, 200, response.text)
        with Session(self.engine) as db:
            messages = db.query(EmailReplyMessage).order_by(EmailReplyMessage.id.asc()).all()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].direction, "outbound")
            conversation = (
                db.query(EmailConversation).filter(EmailConversation.external_thread_id == "thread-123").first()
            )
            assert conversation is not None
            self.assertEqual(conversation.status, "sent")
            self.assertEqual(conversation.unread_reply_count, 0)

    def test_only_replies_filter_sorts_unread_first_then_by_true_reply_time(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db)
            no_reply_email = self._add_sent_email(
                db, token="tok-no-reply", external_message_id="msg-no-reply", external_thread_id="thread-no-reply"
            )
            ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=no_reply_email, thread_id="thread-no-reply"
            )

            old_reply_email = self._add_sent_email(
                db, token="tok-old", external_message_id="msg-old", external_thread_id="thread-old"
            )
            old_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=old_reply_email, thread_id="thread-old"
            )
            db.flush()
            db.add(
                EmailReplyMessage(
                    owner_id=main.settings.owner_id,
                    conversation_id=old_conversation.id,
                    direction="inbound",
                    external_message_id="reply-old",
                    sender="recruiter@example.com",
                    body="old reply",
                    snippet="old reply",
                    received_at=datetime(2026, 1, 1, tzinfo=UTC),
                )
            )
            old_conversation.status = "replied"
            old_conversation.unread_reply_count = 0
            # Replying back bumps last_message_at without a new inbound message — this must
            # not affect the "Received Replies" ordering, which is what this test guards.
            old_conversation.last_message_at = datetime(2026, 6, 1, tzinfo=UTC)

            unread_email = self._add_sent_email(
                db, token="tok-unread", external_message_id="msg-unread", external_thread_id="thread-unread"
            )
            unread_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=unread_email, thread_id="thread-unread"
            )
            db.flush()
            db.add(
                EmailReplyMessage(
                    owner_id=main.settings.owner_id,
                    conversation_id=unread_conversation.id,
                    direction="inbound",
                    external_message_id="reply-unread",
                    sender="recruiter@example.com",
                    body="unread reply",
                    snippet="unread reply",
                    received_at=datetime(2026, 3, 1, tzinfo=UTC),
                )
            )
            unread_conversation.status = "replied"
            unread_conversation.unread_reply_count = 1
            db.commit()

        listing = self.client.get("/inbox/conversations?sort=unread_first")
        self.assertEqual(listing.status_code, 200, listing.text)
        rows = listing.json()
        thread_order = [row["subject"] for row in rows]
        self.assertEqual(len(rows), 3, thread_order)
        self.assertEqual(rows[0]["unread_reply_count"], 1)  # unread reply sorts first
        self.assertTrue(rows[0]["last_inbound_reply_at"].startswith("2026-03-01"))
        old_row = next(row for row in rows if row["last_inbound_reply_at"] and row["last_inbound_reply_at"].startswith("2026-01-01"))
        self.assertEqual(old_row["unread_reply_count"], 0)

    def test_unread_only_tri_state_is_distinct_for_true_false_and_omitted(self) -> None:
        # Regression guard: a naive `if unread_only:` check treats False the same as
        # unset, silently making "No" a no-op. This asserts all three states differ.
        with Session(self.engine) as db:
            self._add_settings(db)
            read_email = self._add_sent_email(
                db, token="tok-read", external_message_id="msg-read", external_thread_id="thread-read"
            )
            read_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=read_email, thread_id="thread-read"
            )
            read_conversation.unread_reply_count = 0

            unread_email = self._add_sent_email(
                db, token="tok-unread2", external_message_id="msg-unread2", external_thread_id="thread-unread2"
            )
            unread_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=unread_email, thread_id="thread-unread2"
            )
            unread_conversation.unread_reply_count = 2
            db.commit()

        omitted = self.client.get("/inbox/conversations")
        self.assertEqual(omitted.status_code, 200, omitted.text)
        self.assertEqual(len(omitted.json()), 2)

        unread_true = self.client.get("/inbox/conversations?unread_only=true")
        self.assertEqual(unread_true.status_code, 200, unread_true.text)
        self.assertEqual([row["unread_reply_count"] for row in unread_true.json()], [2])

        unread_false = self.client.get("/inbox/conversations?unread_only=false")
        self.assertEqual(unread_false.status_code, 200, unread_false.text)
        self.assertEqual([row["unread_reply_count"] for row in unread_false.json()], [0])

    def test_inbox_text_facets_filter_list_and_refresh_endpoints(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db)
            match = self._add_sent_email(
                db, token="tok-match", external_message_id="msg-match", external_thread_id="thread-match"
            )
            match.role = "Java Developer"
            match.location = "Austin, TX"
            match.interview_type = "Video interview"
            ensure_sent_conversation(db, owner_id=main.settings.owner_id, root_email=match, thread_id="thread-match")
            other = self._add_sent_email(
                db, token="tok-other", external_message_id="msg-other", external_thread_id="thread-other"
            )
            other.role = "Python Developer"
            other.location = "Remote"
            other.interview_type = "Phone screen"
            ensure_sent_conversation(db, owner_id=main.settings.owner_id, root_email=other, thread_id="thread-other")
            db.commit()
            match_id = match.id

        class InboxStub:
            def list_inbox_conversations(self, db: Session, **filters: object):
                return list_conversations(db, main.settings.owner_id, **filters)

            def refresh_inbox_replies(self, db: Session, **filters: object):
                return list_conversations(db, main.settings.owner_id, **filters)

        main.orchestration_service = InboxStub()
        for field, value in (("role", "java"), ("location", "austin"), ("interview_type", "video")):
            for method, path in ((self.client.get, "/inbox/conversations"), (self.client.post, "/inbox/conversations/refresh")):
                response = method(path, params={field: value})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([row["root_recruiter_email_id"] for row in response.json()], [match_id])

    def test_inbox_conversations_filter_by_last_message_date(self) -> None:
        with Session(self.engine) as db:
            self._add_settings(db)
            old_email = self._add_sent_email(
                db, token="tok-old-date", external_message_id="msg-old-date", external_thread_id="thread-old-date"
            )
            old_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=old_email, thread_id="thread-old-date"
            )
            old_conversation.last_message_at = datetime(2026, 1, 15, tzinfo=UTC)

            new_email = self._add_sent_email(
                db, token="tok-new-date", external_message_id="msg-new-date", external_thread_id="thread-new-date"
            )
            new_conversation = ensure_sent_conversation(
                db, owner_id=main.settings.owner_id, root_email=new_email, thread_id="thread-new-date"
            )
            new_conversation.last_message_at = datetime(2026, 3, 15, tzinfo=UTC)
            db.commit()

        listing = self.client.get("/inbox/conversations?date_filter=custom&date_from=2026-03-01&date_to=2026-03-31")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual([row["subject"] for row in listing.json()], ["Java role"])
        self.assertTrue(listing.json()[0]["last_message_at"].startswith("2026-03-15"))


if __name__ == "__main__":
    unittest.main()
