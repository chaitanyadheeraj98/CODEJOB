import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import ChatMessage, ChatSession, EmailConversation, EmailReplyMessage, RecruiterEmail
from app.services.proactive_notification_service import generate_reply_notifications


class ProactiveNotificationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_reply(self, *, external_message_id: str = "msg-1") -> EmailReplyMessage:
        root = RecruiterEmail(
            owner_id=settings.owner_id,
            sender="recruiter@example.com",
            subject="Java Engineer",
            body="body",
            role="Java Engineer",
            state="needs_review",
        )
        self.db.add(root)
        self.db.commit()
        conversation = EmailConversation(
            owner_id=settings.owner_id,
            root_recruiter_email_id=root.id,
            external_thread_id=f"thread-{external_message_id}",
        )
        self.db.add(conversation)
        self.db.commit()
        reply = EmailReplyMessage(
            owner_id=settings.owner_id,
            conversation_id=conversation.id,
            external_message_id=external_message_id,
            sender="recruiter@example.com",
            body="This role is only open to local candidates, are you local?",
        )
        self.db.add(reply)
        self.db.commit()
        return reply

    def _patched_llm(self, reply_text: str):
        fake_llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=reply_text)))
        return patch(
            "app.services.proactive_notification_service.build_chat_llm", return_value=fake_llm
        ), fake_llm

    def test_notify_creates_assistant_message_and_marks_reply_notified(self) -> None:
        reply = self._seed_reply()
        patcher, fake_llm = self._patched_llm(
            "NOTIFY: The recruiter says this role is local-only. Want me to draft a reply confirming your location?"
        )
        with patcher:
            created = generate_reply_notifications(self.db)

        self.assertEqual(created, 1)
        fake_llm.ainvoke.assert_awaited_once()
        self.db.refresh(reply)
        self.assertIsNotNone(reply.notified_at)

        messages = self.db.query(ChatMessage).all()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, "assistant")
        self.assertIn("local-only", messages[0].content)

    def test_no_action_needed_marks_reviewed_without_posting_a_message(self) -> None:
        reply = self._seed_reply()
        patcher, _fake_llm = self._patched_llm("NO_ACTION_NEEDED")
        with patcher:
            created = generate_reply_notifications(self.db)

        self.assertEqual(created, 0)
        self.db.refresh(reply)
        self.assertIsNotNone(reply.notified_at)
        self.assertEqual(self.db.query(ChatMessage).count(), 0)

    def test_already_notified_replies_are_never_reclassified(self) -> None:
        self._seed_reply()
        patcher, fake_llm = self._patched_llm("NOTIFY: first pass")
        with patcher:
            generate_reply_notifications(self.db)

        patcher2, fake_llm2 = self._patched_llm("NOTIFY: should never run")
        with patcher2:
            created_again = generate_reply_notifications(self.db)

        self.assertEqual(created_again, 0)
        fake_llm2.ainvoke.assert_not_awaited()
        self.assertEqual(self.db.query(ChatMessage).count(), 1)

    def test_reuses_most_recently_updated_session_or_creates_one(self) -> None:
        self._seed_reply()
        patcher, _fake_llm = self._patched_llm("NOTIFY: draft?")
        with patcher:
            generate_reply_notifications(self.db)

        sessions = self.db.query(ChatSession).all()
        self.assertEqual(len(sessions), 1)
        message = self.db.query(ChatMessage).one()
        self.assertEqual(message.session_id, sessions[0].id)


if __name__ == "__main__":
    unittest.main()
