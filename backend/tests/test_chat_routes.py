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
from app.models import ChatMessage
from app.runtime_state import runtime_state


class FakeGraph:
    async def astream(self, state, **_kwargs):
        yield "messages", (AIMessageChunk(content="Hello"), {})
        yield "values", {"messages": [*state["messages"], AIMessage(content="Hello")]}


class ChatRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools_patch = patch("app.ai.chat.agent.get_mcp_tools", new=AsyncMock(return_value=[]))
        self.tools_patch.start()
        self.addCleanup(self.tools_patch.stop)
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
        runtime_state.chat_active_model = None
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_disabled_status_is_explicit_and_sessions_are_gated(self) -> None:
        main.settings.feature_chat_enabled = False
        status = self.client.get("/chat/status")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertFalse(status.json()["enabled"])
        self.assertEqual(self.client.post("/chat/sessions").status_code, 404)

    def test_since_id_returns_only_newer_messages(self) -> None:
        main.settings.feature_chat_enabled = True
        session_id = self.client.post("/chat/sessions").json()["id"]
        db = self.SessionLocal()
        try:
            for index in range(3):
                db.add(ChatMessage(session_id=session_id, role="user", content=f"m{index}"))
            db.commit()
            ids = [
                row.id
                for row in db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(ChatMessage.id)
            ]
        finally:
            db.close()

        full = self.client.get(f"/chat/sessions/{session_id}")
        self.assertEqual([row["id"] for row in full.json()["messages"]], ids)

        # The poll's normal case: only what arrived after the last known id.
        delta = self.client.get(f"/chat/sessions/{session_id}", params={"since_id": ids[0]})
        self.assertEqual([row["id"] for row in delta.json()["messages"]], ids[1:])

        # Caught up: an empty list, but the session metadata still comes back so
        # the caller does not have to treat "nothing new" as a failure.
        caught_up = self.client.get(f"/chat/sessions/{session_id}", params={"since_id": ids[-1]})
        self.assertEqual(caught_up.status_code, 200, caught_up.text)
        self.assertEqual(caught_up.json()["messages"], [])
        self.assertEqual(caught_up.json()["id"], session_id)

        beyond = self.client.get(f"/chat/sessions/{session_id}", params={"since_id": ids[-1] + 500})
        self.assertEqual(beyond.json()["messages"], [])

    def test_since_id_rejects_non_positive_values(self) -> None:
        main.settings.feature_chat_enabled = True
        session_id = self.client.post("/chat/sessions").json()["id"]
        # ge=1 keeps a client that computed its high-water mark from optimistic
        # negative ids from silently asking for the whole thread every poll.
        self.assertEqual(self.client.get(f"/chat/sessions/{session_id}", params={"since_id": 0}).status_code, 422)
        self.assertEqual(self.client.get(f"/chat/sessions/{session_id}", params={"since_id": -3}).status_code, 422)

    def test_since_id_is_scoped_to_the_requested_session(self) -> None:
        main.settings.feature_chat_enabled = True
        first = self.client.post("/chat/sessions").json()["id"]
        second = self.client.post("/chat/sessions").json()["id"]
        db = self.SessionLocal()
        try:
            db.add(ChatMessage(session_id=first, role="user", content="in first"))
            db.commit()
            db.add(ChatMessage(session_id=second, role="user", content="in second"))
            db.commit()
        finally:
            db.close()

        body = self.client.get(f"/chat/sessions/{second}", params={"since_id": 1}).json()
        self.assertEqual([row["content"] for row in body["messages"]], ["in second"])

    def test_rename_session_persists_title_and_rejects_blank(self) -> None:
        main.settings.feature_chat_enabled = True
        session_id = self.client.post("/chat/sessions").json()["id"]

        renamed = self.client.patch(f"/chat/sessions/{session_id}", json={"title": "  Gmail Integration Testing  "})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual(renamed.json()["title"], "Gmail Integration Testing")

        fetched = self.client.get(f"/chat/sessions/{session_id}")
        self.assertEqual(fetched.json()["title"], "Gmail Integration Testing")

        blank = self.client.patch(f"/chat/sessions/{session_id}", json={"title": "   "})
        self.assertEqual(blank.status_code, 422)

        missing = self.client.patch("/chat/sessions/999999", json={"title": "Anything"})
        self.assertEqual(missing.status_code, 404)

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

    def test_session_message_route_fails_over_to_second_model(self) -> None:
        main.settings.feature_chat_enabled = True
        created = self.client.post("/chat/sessions")
        session_id = created.json()["id"]
        with patch(
            "app.ai.chat.agent.build_chat_agent",
            new=AsyncMock(side_effect=[RuntimeError("503 overloaded"), FakeGraph()]),
        ):
            response = self.client.post(
                f"/chat/sessions/{session_id}/messages",
                json={"text": "Hi"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('event: message\ndata: {"delta":"Hello"}\n\n', response.text)
        self.assertNotIn("temporarily unavailable", response.text)

        status = self.client.get("/chat/status")
        self.assertEqual(status.json()["model"], main.settings.ollama_chat_model_fallback)

    def test_status_lists_all_configured_models(self) -> None:
        """The picker offers the ladder *and* everything else configured.

        This used to assert the list was exactly the three ladder rungs, which
        made the picker unable to offer a model nobody had promoted to a
        fallback yet. The ladder still leads, because those are the models the
        turn will actually try on "auto".
        """
        main.settings.feature_chat_enabled = True
        offered = self.client.get("/chat/status").json()["available_models"]
        ladder = [
            main.settings.ollama_chat_model,
            main.settings.ollama_chat_model_fallback,
            main.settings.ollama_chat_model_fallback2,
        ]
        self.assertEqual(offered[: len(ladder)], ladder)
        for model in main.settings.ollama_selectable_models.split(","):
            self.assertIn(model.strip(), offered)
        # Deduped: a ladder model also named in the selectable list appears once.
        self.assertEqual(len(offered), len(set(offered)))

    def test_manual_model_selection_does_not_fail_over_on_error(self) -> None:
        main.settings.feature_chat_enabled = True
        created = self.client.post("/chat/sessions")
        session_id = created.json()["id"]
        build_mock = AsyncMock(side_effect=RuntimeError("503 overloaded"))
        with patch("app.ai.chat.agent.build_chat_agent", new=build_mock):
            response = self.client.post(
                f"/chat/sessions/{session_id}/messages",
                json={"text": "Hi", "model": main.settings.ollama_chat_model_fallback2},
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("temporarily unavailable", response.text)
        # The second argument is the user's candidate profile, empty here because
        # this fixture's settings row has none.
        self.assertEqual(build_mock.await_count, 1)
        self.assertEqual(build_mock.call_args.args, (main.settings.ollama_chat_model_fallback2, ""))


if __name__ == "__main__":
    unittest.main()
