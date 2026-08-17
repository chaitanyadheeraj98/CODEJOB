from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from app.models import ChatMessage


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


def db_messages_to_langchain(messages: list[ChatMessage]) -> list[BaseMessage]:
    history: list[BaseMessage] = []
    for row in messages:
        if row.role == "user":
            history.append(HumanMessage(content=row.content))
        elif row.role == "assistant" and row.content:
            history.append(AIMessage(content=row.content))
    return history


def langchain_message_to_db_row(session_id: int, message: BaseMessage) -> ChatMessage | None:
    if isinstance(message, AIMessage):
        tool_calls = getattr(message, "tool_calls", None) or []
        return ChatMessage(
            session_id=session_id,
            role="assistant",
            content=message_text(message.content),
            tool_call_args=json.dumps(tool_calls, separators=(",", ":")) if tool_calls else None,
        )
    if isinstance(message, ToolMessage):
        return ChatMessage(
            session_id=session_id,
            role="tool",
            content=message_text(message.content),
            tool_name=message.name,
            tool_call_args=json.dumps(
                {"tool_call_id": message.tool_call_id}, separators=(",", ":")
            ),
        )
    return None
