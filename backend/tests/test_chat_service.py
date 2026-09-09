import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["DEBUG"] = "false"

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.models import ChatMessage
from app.services.chat_service import ChatService


class FakeGraph:
    async def astream(self, state, **_kwargs):
        yield "messages", (AIMessageChunk(content="There are 3 candidates."), {})
        yield "values", {
            "messages": [
                *state["messages"],
                AIMessage(content="", tool_calls=[{"name": "search_candidates", "args": {}, "id": "call-1"}]),
                ToolMessage(content='{"count":3}', name="search_candidates", tool_call_id="call-1"),
                AIMessage(content="There are 3 candidates."),
            ]
        }


class ChatServiceTests(unittest.TestCase):
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
        self.service = ChatService()

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_send_message_streams_sse_and_persists_user_assistant_and_tool(self) -> None:
        with self.SessionLocal() as db:
            session = self.service.create_session(db)
            with patch("app.ai.chat.agent.build_chat_agent", new=AsyncMock(return_value=FakeGraph())):
                async def collect() -> list[str]:
                    return [chunk async for chunk in self.service.send_message(db, session.id, "Count review candidates")]

                chunks = asyncio.run(collect())

            self.assertIn('event: message\ndata: {"delta":"There are 3 candidates."}', "".join(chunks))
            self.assertIn("event: done", chunks[-1])
            rows = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.id).all()
            self.assertEqual([row.role for row in rows], ["user", "assistant", "tool"])
            self.assertEqual(rows[1].content, "There are 3 candidates.")
            self.assertEqual(rows[2].tool_name, "search_candidates")

    def test_message_limit_is_enforced_before_model_use(self) -> None:
        previous = settings.chat_message_char_limit
        settings.chat_message_char_limit = 4
        try:
            with self.assertRaisesRegex(Exception, "4-character limit"):
                self.service.validate_message("12345")
        finally:
            settings.chat_message_char_limit = previous


if __name__ == "__main__":
    unittest.main()
