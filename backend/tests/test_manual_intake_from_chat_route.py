import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import ChatAttachment, ChatMessage, ChatSession
from app.schemas import ManualRequirementFromChatRequest


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enqueue(self, task, **kwargs):
        self.calls.append({"task": task, **kwargs})
        return SimpleNamespace(id=kwargs["job_id"])


class ManualIntakeFromChatRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        with self.SessionLocal() as db:
            session = ChatSession(owner_id=main.settings.owner_id)
            db.add(session)
            db.commit()
            self.session_id = session.id

    def tearDown(self) -> None:
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _say(self, content: str, role: str = "user") -> int:
        with self.SessionLocal() as db:
            row = ChatMessage(session_id=self.session_id, role=role, content=content)
            db.add(row)
            db.commit()
            return row.id

    def _attach(self, content: str = "Role: Cloud Engineer") -> int:
        with self.SessionLocal() as db:
            row = ChatAttachment(
                owner_id=main.settings.owner_id,
                session_id=self.session_id,
                file_path="/tmp/requirement.pdf",
                file_name="requirement.pdf",
                mime_type="application/pdf",
                byte_size=100,
                sha256="b" * 64,
                content_markdown=content,
            )
            db.add(row)
            db.commit()
            return row.id

    def _post(self, body: dict[str, object], *, busy: bool = False):
        queue = _FakeQueue()
        with (
            patch.object(main, "active_job_id", return_value="manual-job" if busy else None),
            patch.object(main, "get_queue", return_value=queue),
        ):
            response = self.client.post("/manual-requirements/from-chat", json=body)
        return response, queue

    def test_an_attachment_id_enqueues_the_stored_markdown(self) -> None:
        attachment = self._attach("# Cloud role\nKubernetes required")
        response, queue = self._post({"attachment_id": attachment})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queue.calls[0]["kwargs"]["text"], "# Cloud role\nKubernetes required")

    def test_a_message_id_enqueues_that_rows_content(self) -> None:
        message = self._say("Role: Java Engineer\nRate: $88/hr")
        response, queue = self._post({"message_id": message})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queue.calls[0]["kwargs"]["text"], "Role: Java Engineer\nRate: $88/hr")

    def test_confirmation_keeps_the_original_message_after_a_newer_one_arrives(self) -> None:
        original_text = "Role: Original Platform Engineer\nRequirement: ORIGINAL-42"
        original = self._say(original_text)
        self._say("wait, one sec")
        response, queue = self._post({"message_id": original})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(queue.calls[0]["kwargs"]["text"], original_text)

    def test_a_deleted_attachment_returns_404(self) -> None:
        attachment = self._attach()
        with self.SessionLocal() as db:
            db.delete(db.get(ChatAttachment, attachment))
            db.commit()
        response, queue = self._post({"attachment_id": attachment})
        self.assertEqual(response.status_code, 404)
        self.assertIn("no longer readable", response.json()["detail"])
        self.assertEqual(queue.calls, [])

    def test_over_length_text_returns_400_with_both_numbers(self) -> None:
        message = self._say("x" * 11)
        with patch.object(main.settings, "manual_intake_max_chars", 10):
            response, queue = self._post({"message_id": message})
        self.assertEqual(response.status_code, 400)
        self.assertIn("11", response.json()["detail"])
        self.assertIn("10", response.json()["detail"])
        self.assertEqual(queue.calls, [])

    def test_an_empty_message_returns_400(self) -> None:
        message = self._say("   \n  ")
        response, queue = self._post({"message_id": message})
        self.assertEqual(response.status_code, 400)
        self.assertIn("empty", response.json()["detail"])
        self.assertEqual(queue.calls, [])

    def test_acknowledging_a_duplicate_is_logged_and_does_not_block(self) -> None:
        message = self._say("Role: Data Engineer")
        with self.assertLogs(main.logger.name, level="INFO") as logs:
            response, queue = self._post(
                {"message_id": message, "acknowledged_duplicate_of": 42}
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(queue.calls), 1)
        self.assertTrue(any("existing_email_id=42 source=chat" in line for line in logs.output))

    def test_a_busy_manual_intake_queue_returns_409(self) -> None:
        message = self._say("Role: Data Engineer")
        response, queue = self._post({"message_id": message}, busy=True)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "another_job_in_progress")
        self.assertEqual(queue.calls, [])

    def test_the_request_model_has_no_text_field(self) -> None:
        self.assertNotIn("text", ManualRequirementFromChatRequest.model_fields)

    def test_neither_id_returns_422_and_enqueues_nothing(self) -> None:
        response, queue = self._post({"acknowledged_duplicate_of": 42})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(queue.calls, [])

    def test_both_ids_return_422_and_enqueue_nothing(self) -> None:
        message = self._say("Role: Data Engineer")
        attachment = self._attach()
        response, queue = self._post({"message_id": message, "attachment_id": attachment})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(queue.calls, [])


if __name__ == "__main__":
    unittest.main()
