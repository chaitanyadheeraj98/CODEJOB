"""The assistant's tools, reached in-process rather than over loopback HTTP.

This used to post to `http://127.0.0.1:8000/mcp` — the API calling **itself**,
with no session cookie on the request.

That was wrong in two ways at once, and sign-in turned the quiet one into the
loud one:

- **Before sign-in shipped**, the loopback request had no session either, so
  `tenancy.owner_id()` inside every tool fell back to the configured constant.
  The assistant would have read *the legacy owner's data for every signed-in
  user* - a cross-tenant read with no error and no log line.
- **After B6b**, the middleware refuses an unauthenticated request, so the same
  call returns 401 and the user sees "the assistant's tools are not reachable".

The 401 is the lucky outcome. The fix is emphatically **not** to make `/mcp`
public: the tool surface reads tenant data, and exempting it would restore the
leak and widen it to the internet.

Removing the hop removes the problem. An in-process session runs the tools in
**this task**, so the `owner_id` ContextVar set by the session middleware is
simply inherited, and no request, header or token has to carry it - which is
what rule 1 asks for.

Why a session per turn
----------------------
A session started once at application start-up would be worse than useless: the
MCP server runs in the task that *created* the session, so it would capture the
start-up context and every tool would see the fallback owner forever. Measured,
not assumed - a probe with a session created outside the caller's context saw
`FALLBACK` where one created inside saw the real owner.

So the session is created inside the turn and torn down with it. That costs
about 1.5 ms to open and 3-11 ms to build 52 tool schemas, against turns that
run for seconds - and it replaces an HTTP round trip on *every tool call*. The
old module-level cache existed to avoid those round trips and has nothing left
to do.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.shared.memory import create_connected_server_and_client_session


@asynccontextmanager
async def mcp_tools() -> AsyncIterator[list[BaseTool]]:
    """Tools bound to a session that lives exactly as long as this turn.

    The caller must hold the context open for as long as it uses the tools:
    they talk over the session's streams, and closing it closes them.
    """
    # Imported here rather than at module scope: the server module pulls in
    # every tool and the services behind them, and this module is imported by
    # the chat agent, which is imported by far more than the MCP server needs.
    from app.mcp_server.server import mcp

    # `_mcp_server` is the low-level Server behind FastMCP. FastMCP exposes no
    # public accessor for it, and the in-memory transport needs the low-level
    # object; `call_tool`/`list_tools` on the FastMCP wrapper would mean
    # reimplementing the content-block handling that load_mcp_tools does.
    async with create_connected_server_and_client_session(mcp._mcp_server) as session:
        yield await load_mcp_tools(session)
