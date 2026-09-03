from app.mcp_server.tools.candidates import (
    count_received_emails,
    get_candidate,
    get_draft_status,
    propose_bulk_approve_candidates,
    search_candidates,
)
from app.mcp_server.tools.email_actions import propose_send_email
from app.mcp_server.tools.external_feed import list_external_opportunities
from app.mcp_server.tools.help import get_app_help
from app.mcp_server.tools.charts import get_chart
from app.mcp_server.tools.analysis import compare_records, rank_opportunities
from app.mcp_server.tools.metrics import get_metrics
from app.mcp_server.tools.inbox import get_conversation, get_recruiter_replies, list_conversations
from app.mcp_server.tools.navigation import navigate_to_queue
from app.mcp_server.tools.chat_attachments import list_chat_attachments, read_chat_attachment
from app.mcp_server.tools.render import render_candidate_table
from app.mcp_server.tools.premium_numbers import (
    get_record_details,
    list_contact_numbers,
    list_recruiter_opportunities,
    propose_create_premium_contact,
)
from app.mcp_server.tools.resumes import get_resume, list_resumes
from app.mcp_server.tools.runs import get_recent_runs, get_run_items
from app.mcp_server.tools.status import get_ai_status, get_settings_summary
from app.mcp_server.tools.support import propose_create_github_issue

__all__ = [
    "compare_records",
    "count_received_emails",
    "get_ai_status",
    "get_app_help",
    "get_candidate",
    "get_chart",
    "get_conversation",
    "get_draft_status",
    "get_metrics",
    "get_recent_runs",
    "get_record_details",
    "get_resume",
    "get_recruiter_replies",
    "get_run_items",
    "get_settings_summary",
    "list_chat_attachments",
    "list_contact_numbers",
    "list_conversations",
    "list_external_opportunities",
    "list_recruiter_opportunities",
    "list_resumes",
    "navigate_to_queue",
    "propose_bulk_approve_candidates",
    "propose_create_github_issue",
    "propose_create_premium_contact",
    "propose_send_email",
    "rank_opportunities",
    "read_chat_attachment",
    "render_candidate_table",
    "search_candidates",
]
