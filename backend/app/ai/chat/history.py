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
        elif row.role == "event" and row.content:
            # A bracketed system note inside a HumanMessage, the same shape
            # ChatAttachmentService.note_for uses to tell the model about
            # attachment ids. It is what a later turn has to answer "did that
            # actually save?" from.
            history.append(HumanMessage(content=row.content))
    # `tool` rows stay unreplayed on purpose. They carry whole proposal
    # payloads - a profile card holds the complete 20,000-character document -
    # and replaying them would quietly make every confirmation card a permanent
    # cost on every later prompt.
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
