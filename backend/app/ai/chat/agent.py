from __future__ import annotations

import asyncio
import json

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from time import perf_counter

from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolMessage
from langgraph.prebuilt import ToolNode, create_react_agent

from app.ai.chat.failures import FAILURE_MESSAGES, FALLBACK_MESSAGE, _RETRYABLE, _TERMINAL, classify_ollama_error, tool_error
from app.ai.chat.history import message_text
from app.ai.chat.llm import build_chat_llm
from app.ai.chat.mcp_client import get_mcp_tools, tools_cached
from app.ai.chat.system_prompt import build_system_prompt, prompt_sha256
from app.config import settings
from app.runtime_state import runtime_state


# Everything a finished tool is allowed to say about itself on screen. The
# repair envelope's own vocabulary plus the two outcomes a tool that returned
# nothing structured can have. Anything else is reported as "ok", because this
# string is rendered as the app speaking rather than as tool output.
TOOL_STATUSES = frozenset({"ok", "error", "success", "missing_fields", "refused"})


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


def selectable_models() -> list[str]:
    """Every model the picker offers, deduped, ladder first.

    Kept separate from `chat_models()` on purpose. That function answers "what
    will this turn try", and pinning the picker to it meant the user could only
    choose one of the three rungs. This answers "what may the user choose", and
    a model listed here that is not on the ladder is selectable but never
    automatic - which is the right default for a model nobody has measured yet.
    """
    seen: set[str] = set()
    models: list[str] = []
    for model in [*chat_models(), *settings.ollama_selectable_models.split(",")]:
        model = (model or "").strip()
        if model and model not in seen:
            seen.add(model)
            models.append(model)
    return models


def tool_call_budget() -> int:
    """LangGraph's recursion limit for `ollama_max_tool_iterations` tool calls.

    A limit is counted in graph steps, not tool calls, and one tool call costs
    two of them - the model node that asks and the tool node that answers - plus
    one more step for the model to write the reply afterwards. Under that, the
    prebuilt agent silently swaps the answer for "Sorry, need more steps to
    process this request."

    Passing the setting straight through, as this did, made
    `ollama_max_tool_iterations = 6` mean two tool calls. Any flow that had to
    look something up, read it, and then propose a change could never finish,
    and the failure looked like the model giving up rather than a budget.
    """
    return 2 * max(1, settings.ollama_max_tool_iterations) + 2


async def build_chat_agent(model: str, candidate_profile: str = "", *, tools=None, timeout: float | None = None):
    if tools is None:
        tools = await get_mcp_tools()
    return create_react_agent(
        build_chat_llm(model, timeout), ToolNode(tools, handle_tool_errors=tool_error),
        prompt=build_system_prompt(candidate_profile),
    )


async def stream_chat_agent(
    messages: list[BaseMessage], model: str | None = None, candidate_profile: str = ""
) -> AsyncIterator[tuple[str, object]]:
    started = perf_counter()
    deadline = started + settings.chat_turn_budget_seconds
    runtime_state.chat_last_attempted_at = datetime.now(UTC)
    metrics = {"model": "", "attempts": 0, "failed_over": False, "prompt_tokens": None,
               "completion_tokens": None, "tool_calls": [], "time_to_first_token_ms": None,
               "failure_code": None, "budget_exhausted": False, "mcp_cached": tools_cached(),
               "prompt_sha256": prompt_sha256()}
    yield "telemetry", metrics
    streamed = ""
    latest_messages = []
    usage = {}
    pending_tools = {}
    code = None
    try:
        try:
            async with asyncio.timeout(max(0, deadline - perf_counter())):
                tools = await get_mcp_tools()
            runtime_state.chat_mcp_status = "ready"
        except Exception:
            code = "budget_exhausted" if perf_counter() >= deadline else "mcp_unavailable"
            runtime_state.chat_mcp_status = f"error: {code}"
            raise
        for index, selected in enumerate(chat_models(model)):
            for attempt in range(settings.chat_model_max_attempts):
                metrics.update(model=selected, attempts=metrics["attempts"] + 1, failed_over=index > 0)
                code = None
                latest_messages = []
                announced = len(messages)
                try:
                    remaining = max(0, deadline - perf_counter())
                    async with asyncio.timeout(remaining):
                        graph = await build_chat_agent(selected, candidate_profile, tools=tools, timeout=remaining)
                        async for mode, payload in graph.astream(
                            {"messages": messages}, config={"recursion_limit": tool_call_budget()},
                            stream_mode=["messages", "values"],
                        ):
                            if mode == "messages":
                                chunk, metadata = payload
                                if isinstance(chunk, AIMessageChunk):
                                    if chunk.usage_metadata:
                                        usage[(metrics["attempts"], chunk.id)] = chunk.usage_metadata
                                    delta = message_text(chunk.content)
                                    if delta:
                                        if metrics["time_to_first_token_ms"] is None:
                                            metrics["time_to_first_token_ms"] = int((perf_counter() - started) * 1000)
                                        streamed += delta
                                        yield "delta", delta
                            elif mode == "values" and isinstance(payload, dict):
                                latest_messages = list(payload.get("messages") or [])
                                for position, message in enumerate(latest_messages[announced:], announced):
                                    if isinstance(message, AIMessage) and message.usage_metadata:
                                        usage[(metrics["attempts"], message.id or position)] = message.usage_metadata
                                    for call in getattr(message, "tool_calls", None) or []:
                                        pending_tools[call["id"]] = (call["name"], perf_counter())
                                        yield "tool", call["name"]
                                    if isinstance(message, ToolMessage):
                                        name, tool_started = pending_tools.pop(message.tool_call_id, (message.name or "tool", perf_counter()))
                                        try:
                                            result = json.loads(message_text(message.content))
                                        except (ValueError, TypeError):
                                            result = {}
                                        status = (result.get("status") or ("error" if result.get("error") else message.status)) if isinstance(result, dict) else message.status
                                        record = {"name": name, "duration_ms": int((perf_counter() - tool_started) * 1000), "status": status}
                                        metrics["tool_calls"].append(record)
                                        # `status` is written by our own tools, but it is the one field
                                        # here that a tool could widen into prose, so it is clamped to
                                        # the enumerated set before it can reach the user's screen.
                                        # `name` comes from the registry and `duration_ms` is measured.
                                        yield "tool_done", {**record, "status": status if status in TOOL_STATUSES else "ok"}
                                announced = len(latest_messages)
                                yield "values", latest_messages[len(messages):]
                                generated = latest_messages[len(messages):]
                                steps = 2 * sum(isinstance(m, AIMessage) and bool(m.tool_calls) for m in generated) + 1
                                if (steps >= tool_call_budget() - 1 and generated
                                        and message_text(generated[-1].content) == "Sorry, need more steps to process this request."):
                                    code = "tool_budget_exhausted"
                                    raise RuntimeError(code)
                    runtime_state.chat_last_error = None
                    runtime_state.chat_last_failure_code = None
                    runtime_state.chat_last_success_at = datetime.now(UTC)
                    runtime_state.chat_active_model = selected
                    yield "complete", latest_messages[len(messages):]
                    return
                except Exception as exc:
                    code = code or ("budget_exhausted" if perf_counter() >= deadline else classify_ollama_error(exc))
                    # ponytail: repair only before the first token; after it, preserve and report.
                    if streamed or code in _TERMINAL:
                        raise
                    if code in _RETRYABLE and attempt + 1 < settings.chat_model_max_attempts:
                        await asyncio.sleep(min(2 ** attempt, max(0, deadline - perf_counter()) / 2))
                        continue
                    break
        if code is None:
            code = "ollama_unavailable"
    except Exception as exc:
        code = code or classify_ollama_error(exc)
    finally:
        metrics["duration_ms"] = max(0, int((perf_counter() - started) * 1000))
        metrics["failure_code"] = code
        metrics["budget_exhausted"] = code == "budget_exhausted"
        if usage:
            metrics["prompt_tokens"] = sum(v.get("input_tokens", 0) for v in usage.values())
            metrics["completion_tokens"] = sum(v.get("output_tokens", 0) for v in usage.values())
        for name, tool_started in pending_tools.values():
            metrics["tool_calls"].append({"name": name, "duration_ms": int((perf_counter() - tool_started) * 1000), "status": "interrupted"})
        runtime_state.chat_last_duration_ms = metrics["duration_ms"]
    runtime_state.chat_last_error = code
    runtime_state.chat_last_failure_code = code
    failure = FAILURE_MESSAGES.get(code, FALLBACK_MESSAGE)
    yield "error", {"code": code, "message": failure}
    delta = ("\n\n" if streamed else "") + failure
    yield "delta", delta
    yield "complete", [AIMessage(content=streamed + delta)]
