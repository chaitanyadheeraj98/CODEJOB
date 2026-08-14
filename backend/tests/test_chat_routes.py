import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.runtime_state import runtime_state


class FakeGraph:
    async def astream(self, state, **_kwargs):
        yield "messages", (AIMessageChunk(content="Hello"), {})
        yield "values", {"messages": [*state["messages"], AIMessage(content="Hello")]}


class ChatRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.previous_enabled = main.settings.feature_chat_enabled

    def tearDown(self) -> None:
        main.settings.feature_chat_enabled = self.previous_enabled
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_disabled_status_is_explicit_and_sessions_are_gated(self) -> None:
        main.settings.feature_chat_enabled = False
        status = self.client.get("/chat/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertFalse(status.json()["enabled"])
        self.assertEqual(self.client.post("/chat/sessions").status_code, 404)

    def test_session_message_route_streams_sse(self) -> None:
        main.settings.feature_chat_enabled = True
        with patch("app.routers.chat._ollama_running", new=AsyncMock(return_value=True)):
            status = self.client.get("/chat/status")
        self.assertTrue(status.json()["ollama_running"])

        created = self.client.post("/chat/sessions")
        self.assertEqual(created.status_code, 200, created.text)
        session_id = created.json()["id"]
        with patch("app.ai.chat.agent.build_chat_agent", new=AsyncMock(return_value=FakeGraph())):
            response = self.client.post(
                f"/chat/sessions/{session_id}/messages",
                json={"text": "Hi"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn('event: message\ndata: {"delta":"Hello"}\n\n', response.text)
        self.assertIn("event: done", response.text)


if __name__ == "__main__":
    unittest.main()
