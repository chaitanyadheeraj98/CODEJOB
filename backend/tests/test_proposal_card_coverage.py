"""Every proposal tool the model can call must have a card the user can click.

The defect this exists to prevent has now happened twice, and neither time did
anything fail. `propose_nvoids_search` was written, registered, and shipped with
no handler in the dashboard's `PROPOSAL_HANDLERS` - and because `visibleMessages`
drops a tool row that neither registry knows, the row was deleted from the
session before render. The assistant announced a search "ready to confirm" and
there was nothing on screen to disagree with it. The tool worked; the round trip
did not exist.

Two halves, and the split is deliberate:

* This test is the *build-time* guarantee. It fails before anyone runs the app.
* `unsupportedProposalNotice` is the *run-time* one, for the case this cannot
  cover: a dashboard build older than the backend it is talking to, or a feature
  flag enabling a tool the deployed frontend predates. There the row is kept and
  the app says it cannot draw it.

Neither is sufficient alone. A CI check does not help a user on a stale bundle,
and a graceful degradation is not a reason to ship a tool with no card.

The frontend side is parsed rather than imported, because Python cannot import
TypeScript. `test_the_handler_list_was_actually_parsed` guards that: a regex
that silently matches nothing would make every other assertion here vacuous.
"""

import os
import re
import inspect
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

PROPOSALS_TS = (
    Path(__file__).resolve().parents[2] / "dashboard" / "src" / "features" / "chat" / "proposals.ts"
)

# A top-level key of the PROPOSAL_HANDLERS object literal: two spaces of indent,
# an identifier, then `: {`. Anything nested is indented further.
_HANDLER_KEY = re.compile(r"^  ([A-Za-z_][A-Za-z0-9_]*): \{$", re.MULTILINE)


def registered_proposal_tools() -> set[str]:
    """Every `propose_*` the MCP server can register.

    Read off the server module's namespace rather than a list, because
    registration requires the name to be imported there - the tuples
    (BASE_TOOLS, CHAT_ACTION_TOOLS, ...) and the conditional ad-hoc
    registrations alike. A list would have to be kept in step with the thing it
    describes, which is the failure mode being tested for.
    """
    from app.mcp_server import server

    return {
        name
        for name, value in vars(server).items()
        if name.startswith("propose_") and callable(value)
    }


def handler_keys() -> set[str]:
    source = PROPOSALS_TS.read_text(encoding="utf-8")
    start = source.index("export const PROPOSAL_HANDLERS")
    return set(_HANDLER_KEY.findall(source[start:]))


def action_keys() -> set[str]:
    from app.services.proposal_actions import PROPOSAL_ACTIONS

    return set(PROPOSAL_ACTIONS)


class ProposalCardCoverageTests(unittest.TestCase):
    def test_the_handler_list_was_actually_parsed(self) -> None:
        """Without this the parity test passes by matching nothing at all."""
        self.assertTrue(PROPOSALS_TS.is_file(), f"not found: {PROPOSALS_TS}")
        keys = handler_keys()
        self.assertGreaterEqual(len(keys), 10)
        self.assertIn("propose_send_email", keys)
        self.assertTrue(all(key.startswith("propose_") for key in keys), keys)

    def test_every_registered_proposal_tool_has_a_card(self) -> None:
        registered = registered_proposal_tools()
        handlers = handler_keys()
        actions = action_keys()
        self.assertEqual(
            registered,
            handlers,
            "Registered proposal tools and dashboard handlers differ: "
            f"registered={sorted(registered)}, dashboard={sorted(handlers)}",
        )
        self.assertEqual(
            registered,
            actions,
            "Registered proposal tools and Telegram actions differ: "
            f"registered={sorted(registered)}, telegram={sorted(actions)}",
        )

    def test_no_card_exists_for_a_tool_that_does_not(self) -> None:
        """The other direction. A handler with no tool behind it is dead code
        that looks like coverage, and it is what a rename leaves behind."""
        registered = registered_proposal_tools()
        orphaned = sorted((handler_keys() | action_keys()) - registered)
        self.assertEqual(
            orphaned,
            [],
            "A proposal registry has entries for tools the server does not "
            f"register: {', '.join(orphaned)}",
        )

    def test_executors_do_not_read_server_supplied_routes(self) -> None:
        from app.services.proposal_actions import PROPOSAL_ACTIONS

        offenders = sorted(
            name
            for name, action in PROPOSAL_ACTIONS.items()
            if "endpoint" in inspect.getsource(action.execute)
        )
        self.assertEqual(offenders, [])

    def test_every_proposal_tool_is_exported_by_the_tools_package(self) -> None:
        """`__all__` drifted, and the tool it omitted was the broken one.

        Not a functional break - `server.py` imports by name and does not read
        `__all__` - but it is the list a reader consults to answer "what
        proposal tools are there", and it answered wrongly for the one tool
        where the answer mattered.
        """
        from app.mcp_server import tools

        undeclared = sorted(registered_proposal_tools() - set(tools.__all__))
        self.assertEqual(undeclared, [], f"missing from tools.__all__: {undeclared}")


if __name__ == "__main__":
    unittest.main()
