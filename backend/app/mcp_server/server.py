from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.mcp_server.tools import (
    count_received_emails,
    get_ai_status,
    get_app_help,
    get_candidate,
    get_conversation,
    get_draft_status,
    get_recent_runs,
    get_resume,
    get_recruiter_replies,
    get_run_items,
    get_settings_summary,
    list_contact_numbers,
    list_conversations,
    list_external_opportunities,
    list_recruiter_opportunities,
    list_resumes,
    propose_bulk_approve_candidates,
    propose_create_premium_contact,
    propose_send_email,
    search_candidates,
)


mcp = FastMCP(
    "codejob",
    instructions="Read-only access to the local CodeJob owner's candidates, runs, inbox, and settings.",
    json_response=True,
    stateless_http=True,
)

BASE_TOOLS = (
    search_candidates,
    get_candidate,
    count_received_emails,
    get_draft_status,
    get_recent_runs,
    get_run_items,
    list_conversations,
    get_conversation,
    get_recruiter_replies,
    get_ai_status,
    get_settings_summary,
    list_resumes,
    get_app_help,
    list_contact_numbers,
    list_recruiter_opportunities,
    list_external_opportunities,
    get_resume,
)

CHAT_ACTION_TOOLS = (
    propose_bulk_approve_candidates,
    propose_create_premium_contact,
    propose_send_email,
)

for tool in BASE_TOOLS:
    mcp.tool()(tool)

if settings.feature_chat_actions_enabled:
    for tool in CHAT_ACTION_TOOLS:
        mcp.tool()(tool)
    if settings.searxng_url:
        from app.mcp_server.tools.web_search import search_web

        mcp.tool()(search_web)

mcp_app = mcp.streamable_http_app()
