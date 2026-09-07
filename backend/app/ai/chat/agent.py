from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import perf_counter

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langgraph.prebuilt import create_react_agent

from app.ai.chat.history import message_text
from app.ai.chat.llm import build_chat_llm
from app.ai.chat.mcp_client import get_mcp_tools
from app.ai.chat.system_prompt import build_system_prompt
from app.config import settings
from app.runtime_state import runtime_state


FALLBACK_MESSAGE = "Chat is temporarily unavailable. Check that Ollama is running, then try again."


def chat_models(selected: str | None = None) -> list[str]:
    """Resolve the ordered list of models to try for one chat turn.

    A specific `selected` model (anything but "auto"/blank) pins the turn to
    that single model - no automatic failover. Otherwise returns the
    configured primary + fallbacks in order, deduped.
    """
    choice = (selected or "").strip()
    if choice and choice.lower() != "auto":
        return [choice]
    seen: set[str] = set()
    models = []
    for model in (
        settings.ollama_chat_model,
        settings.ollama_chat_model_fallback,
        settings.ollama_chat_model_fallback2,
    ):
        model = (model or "").strip()
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


async def build_chat_agent(model: str, candidate_profile: str = ""):
    tools = await get_mcp_tools()
    runtime_state.chat_mcp_status = "ready"
    return create_react_agent(
        build_chat_llm(model), tools, prompt=build_system_prompt(candidate_profile)
    )


async def stream_chat_agent(
    messages: list[BaseMessage], model: str | None = None, candidate_profile: str = ""
) -> AsyncIterator[tuple[str, object]]:
    started = perf_counter()
    runtime_state.chat_last_attempted_at = datetime.now(UTC)
    models = chat_models(model)
    try:
        for index, model in enumerate(models):
            yielded_any = False
            try:
                graph = await build_chat_agent(model, candidate_profile)
                latest_messages: list[BaseMessage] = []
                # How far into the state we have already looked for tool calls, so
                # each one is announced once. Starts past the history we sent in.
                announced = len(messages)
                async for mode, payload in graph.astream(
                    {"messages": messages},
                    config={"recursion_limit": max(2, settings.ollama_max_tool_iterations)},
                    stream_mode=["messages", "values"],
                ):
                    if mode == "messages":
                        chunk, _metadata = payload
                        if isinstance(chunk, AIMessageChunk):
                            delta = message_text(chunk.content)
                            if delta:
                                yielded_any = True
                                yield "delta", delta
                    elif mode == "values" and isinstance(payload, dict):
                        latest_messages = list(payload.get("messages") or [])
                        # `values` fires after the agent node decides on a tool and
                        # before the tool node runs it, so this reaches the user at
                        # the start of the wait rather than after it. It matters for
                        # propose_taxonomy_bulk_review, which spends 30s+ inside
                        # DeepSeek with nothing else on the wire.
                        for message in latest_messages[announced:]:
                            for call in getattr(message, "tool_calls", None) or []:
                                name = (
                                    call.get("name")
                                    if isinstance(call, dict)
                                    else getattr(call, "name", None)
                                )
                                if name:
                                    yield "tool", str(name)
                        announced = len(latest_messages)

                runtime_state.chat_last_error = None
                runtime_state.chat_last_success_at = datetime.now(UTC)
                runtime_state.chat_active_model = model
                yield "complete", latest_messages[len(messages):]
                return
            except Exception as exc:
                # ponytail: only fail over before any tokens reach the user; a break after
                # partial output falls straight to FALLBACK_MESSAGE instead of risking a
                # garbled reply stitched from two models.
                if yielded_any or index == len(models) - 1:
                    raise
                runtime_state.chat_last_error = f"{model}: {str(exc)[:500]}"
    except Exception as exc:
        runtime_state.chat_last_error = str(exc)[:2000]
        if runtime_state.chat_mcp_status != "ready":
            runtime_state.chat_mcp_status = f"error: {str(exc)[:180]}"
        yield "delta", FALLBACK_MESSAGE
        yield "complete", [AIMessage(content=FALLBACK_MESSAGE)]
    finally:
        runtime_state.chat_last_duration_ms = max(0, int((perf_counter() - started) * 1000))
