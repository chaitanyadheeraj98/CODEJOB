from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.mcp_server.tools import (
    compare_records,
    check_manual_intake,
    count_received_emails,
    get_ai_status,
    get_app_help,
    get_candidate,
    get_chart,
    get_conversation,
    get_draft_status,
    get_metrics,
    get_recent_runs,
    get_record_details,
    get_relationships,
    get_resume,
    get_recruiter_replies,
    get_run_items,
    get_settings_summary,
    list_candidate_documents,
    list_chat_attachments,
    list_contact_numbers,
    list_conversations,
    list_external_opportunities,
    list_recruiter_opportunities,
    list_scheduled_tasks,
    list_resumes,
    navigate_to_queue,
    check_nvoids_search,
    propose_nvoids_search,
    rank_recruiters,
    search_end_client,
    search_opportunities,
    propose_add_note,
    propose_bulk_approve_candidates,
    propose_candidate_action,
    propose_create_github_issue,
    propose_create_premium_contact,
    propose_manual_requirement,
    propose_profile_update,
    propose_record_update,
    propose_scheduled_task,
    propose_send_email,
    propose_taxonomy_bulk_review,
    rank_opportunities,
    read_chat_attachment,
    recommend_recruiter,
    render_candidate_table,
    resolve_record_reference,
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
    # A read tool, not an action: a table renders whether or not chat actions are
    # enabled. Acting on a selection routes to /candidates/approve-bulk, which is
    # the same endpoint the Needs Review bulk bar already uses.
    render_candidate_table,
    get_candidate,
    get_chart,
    count_received_emails,
    get_draft_status,
    get_metrics,
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
    check_manual_intake,
    check_nvoids_search,
    rank_recruiters,
    search_end_client,
    search_opportunities,
    get_record_details,
    list_external_opportunities,
    get_resume,
    list_chat_attachments,
    read_chat_attachment,
    # Read-only: it draws a button, and the user's click navigates. Registered
    # in BASE_TOOLS so queues stay reachable with chat actions disabled.
    navigate_to_queue,
    rank_opportunities,
    compare_records,
    # Read-only: it returns options, never a pick.
    resolve_record_reference,
)

CHAT_ACTION_TOOLS = (
    # Read-only, but registered here rather than in BASE_TOOLS: its only purpose
    # is turning "attach my passport" into ids for propose_send_email, so with
    # actions off it can answer a question the user cannot act on while adding to
    # the 35-tool routing baseline the comments below are protecting.
    list_candidate_documents,
    propose_bulk_approve_candidates,
    propose_candidate_action,
    propose_record_update,
    propose_add_note,
    propose_create_premium_contact,
    # A write into <user_profile> - the one block the prompt tells the model to
    # believe - so it sits behind the same propose-then-confirm boundary as
    # every other action. What the model contributes is a registry label; the
    # value is checked against the user's own messages on the server.
    propose_profile_update,
    propose_send_email,
    # Previews a stored JD; only the confirmation-card click queues ingestion.
    propose_manual_requirement,
    # An outbound crawl of a third party is a side effect, not a read, so it
    # sits behind the same propose-then-confirm boundary as every other action
    # rather than inventing a second confirmation mechanism for one feature.
    propose_nvoids_search,
    # Writes into the trusted vocabulary the parser matches against, so it sits
    # behind the same propose-then-confirm boundary as every other action. The
    # keys on the card come from the server's own classifier, never from the
    # model - what the model contributes is the scope and the verb.
    propose_taxonomy_bulk_review,
)

# Read-only, so they belong in BASE_TOOLS - but they are registered only when
# the feature is on. v2 left the routing/latency measurement at 35 tools owed,
# and adding to an unmeasured baseline makes any regression unattributable.
# With the flag off the registry stays at exactly the count v2 shipped.
RELATIONSHIP_TOOLS = (
    get_relationships,
    recommend_recruiter,
)

# Same reasoning, one phase later. v1's and v2's routing measurement is still
# owed at 35 tools; registering v4's pair by default would make any regression
# unattributable across two unmeasured additions at once.
SCHEDULING_TOOLS = (
    list_scheduled_tasks,
    propose_scheduled_task,
)

for tool in BASE_TOOLS:
    mcp.tool()(tool)

if settings.feature_relationship_intelligence_enabled:
    for tool in RELATIONSHIP_TOOLS:
        mcp.tool()(tool)

if settings.feature_scheduling_enabled:
    for tool in SCHEDULING_TOOLS:
        mcp.tool()(tool)

if settings.feature_chat_actions_enabled:
    for tool in CHAT_ACTION_TOOLS:
        mcp.tool()(tool)
    if settings.searxng_url:
        from app.mcp_server.tools.web_search import search_web

        mcp.tool()(search_web)
    if settings.github_token and settings.github_repo:
        mcp.tool()(propose_create_github_issue)

mcp_app = mcp.streamable_http_app()
