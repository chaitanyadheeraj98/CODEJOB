import inspect
import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools import manual_intake as manual_intake_tools
from app.models import ChatAttachment, ChatMessage, ChatSession, RecentRun

OTHER_OWNER = "other-owner"


class ManualIntakeChatToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.session_patch = patch.object(manual_intake_tools, "SessionLocal", self.SessionLocal)
        self.preview_patch = patch.object(manual_intake_tools, "preview_duplicate", return_value=None)
        self.session_patch.start()
        self.preview = self.preview_patch.start()

    def tearDown(self) -> None:
        self.preview_patch.stop()
        self.session_patch.stop()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _session(self, owner: str = settings.owner_id) -> int:
        with self.SessionLocal() as db:
            row = ChatSession(owner_id=owner)
            db.add(row)
            db.commit()
            return row.id

    def _say(self, session_id: int, role: str, content: str) -> int:
        with self.SessionLocal() as db:
            row = ChatMessage(session_id=session_id, role=role, content=content)
            db.add(row)
            db.commit()
            return row.id

    def _attach(
        self,
        session_id: int,
        *,
        owner: str = settings.owner_id,
        content: str | None = "Role: Cloud Engineer",
    ) -> int:
        with self.SessionLocal() as db:
            row = ChatAttachment(
                owner_id=owner,
                session_id=session_id,
                file_path="/tmp/requirement.pdf",
                file_name="requirement.pdf",
                mime_type="application/pdf",
                byte_size=100,
                sha256="a" * 64,
                content_markdown=content,
            )
            db.add(row)
            db.commit()
            return row.id

    def test_a_pasted_jd_is_returned_byte_for_byte(self) -> None:
        session = self._session()
        text = "Role: Java Engineer\nRate: $88/hr\nRecruiter: p@example.test"
        message = self._say(session, "user", text)

        payload = manual_intake_tools.propose_manual_requirement(message_id=message)

        self.assertEqual(payload["jd_text"], text)
        self.assertEqual(payload["source"], "chat_message")

    def test_an_attached_jd_uses_the_extracted_markdown(self) -> None:
        session = self._session()
        attachment = self._attach(session, content="# Cloud role\nKubernetes required")

        payload = manual_intake_tools.propose_manual_requirement(attachment_id=attachment)

        self.assertEqual(payload["jd_text"], "# Cloud role\nKubernetes required")
        self.assertEqual(payload["source"], "attachment")

    def test_the_tool_never_accepts_text(self) -> None:
        self.assertNotIn("text", inspect.signature(manual_intake_tools.propose_manual_requirement).parameters)

    def test_another_owners_attachment_is_not_readable(self) -> None:
        session = self._session(OTHER_OWNER)
        attachment = self._attach(session, owner=OTHER_OWNER)
        self.assertEqual(
            manual_intake_tools.propose_manual_requirement(attachment_id=attachment)["status"],
            "no_document",
        )

    def test_an_assistant_message_is_not_readable(self) -> None:
        session = self._session()
        message = self._say(session, "assistant", "Role: invented")
        self.assertEqual(
            manual_intake_tools.propose_manual_requirement(message_id=message)["status"],
            "no_document",
        )

    def test_over_length_text_refuses_without_card_fields(self) -> None:
        session = self._session()
        message = self._say(session, "user", "x" * 11)
        with patch.object(settings, "manual_intake_max_chars", 10):
            payload = manual_intake_tools.propose_manual_requirement(message_id=message)
        self.assertEqual(payload["status"], "too_long")
        self.assertEqual(payload["characters"], 11)
        self.assertEqual(payload["limit"], 10)
        self.assertIn("11", payload["detail"])
        self.assertIn("10", payload["detail"])
        self.assertNotIn("action", payload)
        self.assertNotIn("jd_text", payload)

    def test_nothing_readable_returns_no_document(self) -> None:
        self.assertEqual(manual_intake_tools.propose_manual_requirement()["status"], "no_document")

    def test_a_duplicate_is_reported_on_the_card(self) -> None:
        session = self._session()
        message = self._say(session, "user", "Role: Platform Engineer")
        self.preview.return_value = SimpleNamespace(
            id=42,
            role="Platform Engineer",
            client="Acme",
            created_at=datetime(2026, 9, 6, tzinfo=UTC),
        )

        duplicate = manual_intake_tools.propose_manual_requirement(message_id=message)["duplicate_of"]

        self.assertEqual(duplicate["id"], 42)
        self.assertEqual(duplicate["role"], "Platform Engineer")
        self.assertEqual(duplicate["client"], "Acme")

    def test_fallback_returns_the_resolved_message_id(self) -> None:
        session = self._session()
        message = self._say(session, "user", "Role: Data Engineer")
        payload = manual_intake_tools.propose_manual_requirement()
        self.assertEqual(payload["message_id"], message)

    def test_successful_payloads_carry_exactly_one_source_id(self) -> None:
        session = self._session()
        message = self._say(session, "user", "Role: Data Engineer")
        pasted = manual_intake_tools.propose_manual_requirement(message_id=message)
        attachment = self._attach(session)
        attached = manual_intake_tools.propose_manual_requirement(attachment_id=attachment)
        self.assertEqual((pasted["attachment_id"], pasted["message_id"]), (None, message))
        self.assertEqual((attached["attachment_id"], attached["message_id"]), (attachment, None))

    def test_no_runs_is_not_reported_as_an_empty_result(self) -> None:
        payload = manual_intake_tools.check_manual_intake()
        self.assertFalse(payload["found"])
        self.assertIn("not an intake that found nothing", payload["instruction"])

    def test_a_running_intake_is_not_finished(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                RecentRun(
                    owner_id=settings.owner_id,
                    run_source="manual_intake",
                    run_key="manual_intake:running",
                    status="running",
                    detail="Reading the pasted requirement.",
                )
            )
            db.commit()
        payload = manual_intake_tools.check_manual_intake("manual_intake:running")
        self.assertFalse(payload["finished"])

    def test_a_finished_intake_returns_the_workers_detail_verbatim(self) -> None:
        detail = "Created Needs Review card 87."
        with self.SessionLocal() as db:
            db.add(
                RecentRun(
                    owner_id=settings.owner_id,
                    run_source="manual_intake",
                    run_key="manual_intake:done",
                    status="ok",
                    detail=detail,
                )
            )
            db.commit()
        payload = manual_intake_tools.check_manual_intake("manual_intake:done")
        self.assertTrue(payload["finished"])
        self.assertEqual(payload["detail"], detail)


if __name__ == "__main__":
    unittest.main()
