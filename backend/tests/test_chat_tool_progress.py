import asyncio
import unittest
from unittest import mock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.ai.chat import agent as chat_agent


class FakeGraph:
    """Replays what create_react_agent's astream emits for a tool-using turn."""

    def __init__(self, events):
        self._events = events

    async def astream(self, _state, config=None, stream_mode=None):
        for event in self._events:
            yield event


def drain(events, messages):
    async def run():
        collected = []
        with mock.patch.object(chat_agent, "build_chat_agent", return_value=FakeGraph(events)):
            async for kind, payload in chat_agent.stream_chat_agent(messages, model="test-model"):
                collected.append((kind, payload))
        return collected

    return asyncio.run(run())


class ToolProgressEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.history = [HumanMessage(content="clean up the pending skills")]

    def test_a_tool_call_is_announced_before_its_result_arrives(self) -> None:
        calling = AIMessage(
            content="",
            tool_calls=[{"name": "propose_taxonomy_bulk_review", "args": {}, "id": "call-1"}],
        )
        result = ToolMessage(content="{}", tool_call_id="call-1", name="propose_taxonomy_bulk_review")
        events = [
            ("values", {"messages": [*self.history, calling]}),
            ("values", {"messages": [*self.history, calling, result]}),
        ]
        collected = drain(events, self.history)
        kinds = [kind for kind, _ in collected]
        self.assertIn("tool", kinds)
        # The announcement is what makes the wait legible, so it has to come out
        # before the turn completes rather than alongside the finished answer.
        self.assertLess(kinds.index("tool"), kinds.index("complete"))
        self.assertEqual(
            [payload for kind, payload in collected if kind == "tool"],
            ["propose_taxonomy_bulk_review"],
        )

    def test_each_tool_call_is_announced_exactly_once(self) -> None:
        calling = AIMessage(
            content="",
            tool_calls=[{"name": "search_candidates", "args": {}, "id": "call-1"}],
        )
        result = ToolMessage(content="{}", tool_call_id="call-1", name="search_candidates")
        # The same message reappears in every later `values` payload, so a naive
        # scan of the whole state would re-announce it on each one.
        events = [
            ("values", {"messages": [*self.history, calling]}),
            ("values", {"messages": [*self.history, calling, result]}),
            ("values", {"messages": [*self.history, calling, result, AIMessage(content="done")]}),
        ]
        announced = [payload for kind, payload in drain(events, self.history) if kind == "tool"]
        self.assertEqual(announced, ["search_candidates"])

    def test_two_different_tools_are_both_announced(self) -> None:
        first = AIMessage(content="", tool_calls=[{"name": "search_candidates", "args": {}, "id": "a"}])
        second = AIMessage(content="", tool_calls=[{"name": "get_chart", "args": {}, "id": "b"}])
        events = [
            ("values", {"messages": [*self.history, first]}),
            ("values", {"messages": [*self.history, first, ToolMessage(content="{}", tool_call_id="a")]}),
            ("values", {"messages": [*self.history, first, ToolMessage(content="{}", tool_call_id="a"), second]}),
        ]
        announced = [payload for kind, payload in drain(events, self.history) if kind == "tool"]
        self.assertEqual(announced, ["search_candidates", "get_chart"])

    def test_a_turn_with_no_tool_calls_announces_nothing(self) -> None:
        events = [("values", {"messages": [*self.history, AIMessage(content="hello")]})]
        announced = [payload for kind, payload in drain(events, self.history) if kind == "tool"]
        self.assertEqual(announced, [])


if __name__ == "__main__":
    unittest.main()
