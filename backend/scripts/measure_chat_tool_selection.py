"""Read-only tool-selection measurement. No real tool runs, commits or runtime-state writes.

Run with python -m scripts.measure_chat_tool_selection. Prints routing and token
counts, never question bodies, model answers, credentials or exception text.
"""
import argparse
import asyncio
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool, create_schema_from_function
from langgraph.prebuilt import create_react_agent

from app.ai.chat.agent import chat_models
from app.ai.chat.llm import build_chat_llm
from app.ai.chat.system_prompt import build_system_prompt, prompt_sha256
from app.config import settings
from app.mcp_server.server import BASE_TOOLS, CHAT_ACTION_TOOLS, RELATIONSHIP_TOOLS, SCHEDULING_TOOLS
from app.mcp_server.tools.support import propose_create_github_issue
from app.mcp_server.tools.web_search import search_web

FIXTURES = Path(__file__).parents[1] / "tests/fixtures/chat_tool_selection.json"


def stub_tools(calls):
    functions = list(BASE_TOOLS)
    if settings.feature_chat_actions_enabled:
        functions.extend(CHAT_ACTION_TOOLS)
        if settings.searxng_url:
            functions.append(search_web)
        if settings.github_token and settings.github_repo:
            functions.append(propose_create_github_issue)
    if settings.feature_relationship_intelligence_enabled:
        functions.extend(RELATIONSHIP_TOOLS)
    if settings.feature_scheduling_enabled:
        functions.extend(SCHEDULING_TOOLS)

    def stub(function):
        def run(**kwargs):
            calls.append(function.__name__)
            return json.dumps({"status": "ok", "count": 1, "record_id": "fixture-1",
                               "id": 1, "draft_id": 1, "candidate_email_id": 1,
                               "rows": [], "resumes": [], "recruiters": [], "finished": True,
                               "message": "Fixture result. No action was executed."})
        return StructuredTool.from_function(
            run, name=function.__name__, description=function.__doc__ or function.__name__,
            args_schema=create_schema_from_function(function.__name__, function),
        )
    return [stub(function) for function in functions]


async def measure_case(model, case, timeout=30.0):
    calls = []
    tools = stub_tools(calls)
    names = {tool.name for tool in tools}
    if not names.intersection(case["expected"]):
        return {"case": case["id"], "calls": [], "agreed": False, "error": "tool_disabled", "prompt_tokens": 0, "completion_tokens": 0}
    error = ""
    messages = []
    try:
        graph = create_react_agent(build_chat_llm(model, timeout), tools, prompt=build_system_prompt())
        async with asyncio.timeout(timeout):
            result = await graph.ainvoke({"messages": [HumanMessage(content=case["question"])]},
                                        config={"recursion_limit": 8})
            messages = result["messages"]
    except Exception as exc:
        error = type(exc).__name__
    usage = [m.usage_metadata for m in messages if isinstance(m, AIMessage) and m.usage_metadata]
    return {"case": case["id"], "calls": calls, "agreed": bool(calls and calls[0] in case["expected"]),
            "error": error, "prompt_tokens": sum(u["input_tokens"] for u in usage),
            "completion_tokens": sum(u["output_tokens"] for u in usage)}


async def measure(models, cases, timeout=30.0):
    print(f"prompt_sha256={prompt_sha256()}", flush=True)
    print("| Model | Cases | Agreed | Disabled | Errors | Prompt tokens | Completion tokens |", flush=True)
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: |", flush=True)
    for model in models:
        results = []
        for case in cases:
            result = await measure_case(model, case, timeout)
            results.append(result)
            print(json.dumps({"model": model, **result}), flush=True)
        print(f"| {model} | {len(results)} | {sum(r['agreed'] for r in results)} | "
              f"{sum(r['error'] == 'tool_disabled' for r in results)} | "
              f"{sum(bool(r['error']) and r['error'] != 'tool_disabled' for r in results)} | "
              f"{sum(r['prompt_tokens'] for r in results)} | {sum(r['completion_tokens'] for r in results)} |", flush=True)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if args.limit < 1 or args.timeout <= 0:
        parser.error("limit and timeout must be positive")
    cases = json.loads(FIXTURES.read_text(encoding="utf-8"))[:args.limit]
    asyncio.run(measure(args.model or chat_models(), cases, args.timeout))


if __name__ == "__main__":
    main()
