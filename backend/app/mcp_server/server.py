from mcp.server.fastmcp import FastMCP

from app.mcp_server.tools import (
    count_received_emails,
    get_ai_status,
    get_candidate,
    get_conversation,
    get_draft_status,
    get_recent_runs,
    get_run_items,
    get_settings_summary,
    list_conversations,
    search_candidates,
)


mcp = FastMCP(
    "codejob",
    instructions="Read-only access to the local CodeJob owner's candidates, runs, inbox, and settings.",
    json_response=True,
    stateless_http=True,
)

for tool in (
    search_candidates,
    get_candidate,
    count_received_emails,
    get_draft_status,
    get_recent_runs,
    get_run_items,
    list_conversations,
    get_conversation,
    get_ai_status,
    get_settings_summary,
):
    mcp.tool()(tool)

mcp_app = mcp.streamable_http_app()
