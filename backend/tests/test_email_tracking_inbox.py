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
from app.services.email_inbox_service import ensure_sent_conversation, generate_tracking_token, tracking_pixel_url


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

    def _add_sent_email(self, db: Session, *, token: str = "tracking-token") -> RecruiterEmail:
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
            external_message_id="source-message",
            external_thread_id="thread-123",
            recipient_email="recruiter@example.com",
            cc_email="manager@example.com",
            sent_at=datetime.now(UTC),
            gmail_sent_id="sent-message",
            tracking_token=token,
        )
        db.add(row)
        db.flush()
        return row

    def test_tracking_token_and_public_url_are_safe_by_default(self) -> None:
        token = generate_tracking_token("secret", 42)
        self.assertEqual(len(token), 22)
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
            main.orchestration_service = None
            response = self.client.post("/gmail/sync")
        finally:
            (
                main.is_gmail_configured,
                main.list_unread_candidates_by_query,
                main.classify_email_intent,
                main.mark_reply_processed,
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


if __name__ == "__main__":
    unittest.main()
