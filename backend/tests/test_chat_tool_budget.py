"""The tool-call budget for one chat turn.

`ollama_max_tool_iterations` is counted in tool calls, but LangGraph's
recursion_limit is counted in graph steps, and one tool call is two of them plus
one more for the reply. Passing the setting straight through made a setting of 6
mean two calls, and every flow that had to look something up, read it, and then
propose a change died on the third with "Sorry, need more steps to process this
request." - which reads as the model giving up rather than a budget being hit.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.ai.chat.agent import tool_call_budget
from app.config import settings

GIVING_UP = "Sorry, need more steps to process this request."


def run_agent(limit: int, tool_calls_wanted: int) -> tuple[int, str]:
    """Drive a real agent that wants `tool_calls_wanted` calls, then answers."""
    made: list[int] = []

    @tool
    def probe(n: int = 0) -> str:
        """Stands in for any CodeJob tool."""
        made.append(n)
        return "ok"

    class WantsNCalls(GenericFakeChatModel):
        def _call(self, messages, stop=None, run_manager=None, **kwargs):
            return "unused"

        def bind_tools(self, tools, **kwargs):
            return self

        def invoke(self, input, config=None, **kwargs):
            if len(made) >= tool_calls_wanted:
                return AIMessage(content="final answer")
            return AIMessage(
                content="",
                tool_calls=[{"name": "probe", "args": {"n": len(made)}, "id": f"c{len(made)}"}],
            )

    agent = create_react_agent(WantsNCalls(messages=iter([])), [probe])
    try:
        result = agent.invoke(
            {"messages": [HumanMessage(content="go")]}, config={"recursion_limit": limit}
        )
        return len(made), result["messages"][-1].content
    except Exception as exc:  # a limit below the floor raises rather than answers
        return len(made), f"<{type(exc).__name__}>"


class ToolCallBudgetTests(unittest.TestCase):
    def test_the_budget_is_two_steps_per_call_plus_one_for_the_reply(self) -> None:
        for iterations, expected in ((1, 4), (3, 8), (6, 14)):
            with self.subTest(iterations=iterations):
                original = settings.ollama_max_tool_iterations
                settings.ollama_max_tool_iterations = iterations
                try:
                    self.assertEqual(tool_call_budget(), expected)
                finally:
                    settings.ollama_max_tool_iterations = original

    def test_the_setting_delivers_the_number_of_calls_it_names(self) -> None:
        """The regression: 6 used to buy 2 calls, not 6."""
        original = settings.ollama_max_tool_iterations
        settings.ollama_max_tool_iterations = 6
        try:
            made, answer = run_agent(tool_call_budget(), tool_calls_wanted=6)
        finally:
            settings.ollama_max_tool_iterations = original

        self.assertEqual(made, 6)
        self.assertEqual(answer, "final answer")

    def test_the_three_call_resume_flow_now_finishes(self) -> None:
        """Read a draft, propose a rewrite, answer - what used to hit the limit."""
        made, answer = run_agent(tool_call_budget(), tool_calls_wanted=3)

        self.assertEqual(made, 3)
        self.assertNotEqual(answer, GIVING_UP)

    def test_the_old_arithmetic_is_what_produced_the_giving_up_message(self) -> None:
        """Guards the diagnosis, so a revert fails here rather than in the UI."""
        old_limit = max(2, 6)  # what the code used to pass

        made, answer = run_agent(old_limit, tool_calls_wanted=3)

        self.assertEqual(made, 2)
        self.assertEqual(answer, GIVING_UP)

    def test_a_zero_setting_still_leaves_room_for_one_call(self) -> None:
        original = settings.ollama_max_tool_iterations
        settings.ollama_max_tool_iterations = 0
        try:
            budget = tool_call_budget()
        finally:
            settings.ollama_max_tool_iterations = original

        made, answer = run_agent(budget, tool_calls_wanted=1)
        self.assertEqual(made, 1)
        self.assertEqual(answer, "final answer")


if __name__ == "__main__":
    unittest.main()
