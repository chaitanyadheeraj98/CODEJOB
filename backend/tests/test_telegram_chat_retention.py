import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tenancy
from app.config import settings
from app.models import Base, ChatMessage, ChatSession, ChatTurn, TelegramLink
from app.services.telegram_chat_service import purge_expired


class TelegramChatRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.now = datetime.now(UTC)

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _session(self, db, owner_id: str, origin: str = "telegram", *, recent: bool = False):
        touched = self.now - (timedelta(hours=1) if recent else timedelta(days=100))
        row = ChatSession(owner_id=owner_id, origin=origin, created_at=touched, updated_at=touched)
        db.add(row)
        db.flush()
        message = ChatMessage(
            session_id=row.id,
            role="user",
            content="old",
            created_at=self.now - timedelta(days=100),
        )
        db.add(message)
        db.flush()
        return row, message

    def _purge(self, db, owner_id: str) -> dict[str, int]:
        with tenancy.owner_scope(owner_id), patch.object(settings, "telegram_chat_retention_days", 90):
            return purge_expired(db, now=self.now)

    def test_old_telegram_messages_are_deleted_and_web_messages_are_untouched(self) -> None:
        with self.Session() as db:
            telegram, _ = self._session(db, "usr_a")
            web, web_message = self._session(db, "usr_a", "web")
            db.commit()

            self.assertEqual(self._purge(db, "usr_a"), {"messages": 1, "sessions": 1})
            self.assertIsNone(db.query(ChatSession).filter_by(id=telegram.id).first())
            self.assertIsNotNone(db.query(ChatSession).filter_by(id=web.id).first())
            self.assertIsNotNone(db.query(ChatMessage).filter_by(id=web_message.id).first())

    def test_bound_session_survives_when_its_messages_expire(self) -> None:
        with self.Session() as db:
            session, message = self._session(db, "usr_a")
            db.add(TelegramLink(owner_id="usr_a", chat_id=1, chat_session_id=session.id))
            turn = ChatTurn(
                owner_id="usr_a",
                session_id=session.id,
                message_id=message.id,
                prompt_sha256="0" * 64,
            )
            db.add(turn)
            db.commit()

            self._purge(db, "usr_a")
            self.assertIsNotNone(db.query(ChatSession).filter_by(id=session.id).first())
            self.assertIsNone(db.query(ChatMessage).filter_by(id=message.id).first())
            db.expire(turn, ["message_id"])
            self.assertIsNone(turn.message_id)

    def test_recent_session_survives_with_its_messages(self) -> None:
        with self.Session() as db:
            session, message = self._session(db, "usr_a", recent=True)
            db.commit()

            self._purge(db, "usr_a")
            self.assertIsNotNone(db.query(ChatSession).filter_by(id=session.id).first())
            self.assertIsNotNone(db.query(ChatMessage).filter_by(id=message.id).first())

    def test_purging_one_owner_leaves_the_other_owners_rows(self) -> None:
        with self.Session() as db:
            first, _ = self._session(db, "usr_a")
            second, second_message = self._session(db, "usr_b")
            db.commit()

            self._purge(db, "usr_a")
            self.assertIsNone(db.query(ChatSession).filter_by(id=first.id).first())
            self.assertIsNotNone(db.query(ChatSession).filter_by(id=second.id).first())
            self.assertIsNotNone(db.query(ChatMessage).filter_by(id=second_message.id).first())


if __name__ == "__main__":
    unittest.main()
