import logging
import re

_TAG = re.compile(r"</?untrusted_[a-z_]+>", re.I)


def untrusted(kind: str, body: str) -> str:
    tag = kind if kind.startswith("untrusted_") else f"untrusted_{kind}_data"
    body, count = _TAG.subn("", body)
    if count:
        logging.getLogger(__name__).warning("Removed %s untrusted delimiters from %s payload", count, tag)
    return f"<{tag}>\n{body}\n</{tag}>"


def needs(fields: list[str], *, hint: str) -> dict[str, object]:
    return {"status": "missing_fields", "fields": fields, "hint": hint}


def refused(reason: str, *, hint: str) -> dict[str, object]:
    return {"status": "refused", "reason": reason, "hint": hint}


from app.mcp_server.tools.candidates import (
    count_received_emails,
    get_candidate,
    get_draft_status,
    propose_bulk_approve_candidates,
    search_candidates,
)
from app.mcp_server.tools.email_actions import propose_new_email, propose_send_email
from app.mcp_server.tools.external_feed import list_external_opportunities
from app.mcp_server.tools.help import get_app_help
from app.mcp_server.tools.candidate_actions import propose_candidate_action
from app.mcp_server.tools.candidate_profile import propose_profile_update
from app.mcp_server.tools.charts import get_chart
from app.mcp_server.tools.record_actions import propose_add_note, propose_record_update
from app.mcp_server.tools.records import propose_track_record, resolve_record_by_message_id
from app.mcp_server.tools.analysis import compare_records, rank_opportunities
from app.mcp_server.tools.applications import get_tracked_application, list_tracked_applications
from app.mcp_server.tools.metrics import get_metrics
from app.mcp_server.tools.inbox import get_conversation, get_recruiter_replies, list_conversations
from app.mcp_server.tools.manual_intake import check_manual_intake, propose_manual_requirement
from app.mcp_server.tools.labels import get_label_thread_dossier, list_gmail_labels, list_label_threads
from app.mcp_server.tools.navigation import navigate_to_queue
from app.mcp_server.tools.candidate_documents import list_candidate_documents
from app.mcp_server.tools.chat_attachments import list_chat_attachments, read_chat_attachment
from app.mcp_server.tools.render import render_candidate_table
from app.mcp_server.tools.premium_numbers import (
    get_record_details,
    list_contact_numbers,
    list_recruiter_opportunities,
    propose_create_premium_contact,
    rank_recruiters,
    check_nvoids_search,
    propose_nvoids_search,
    search_end_client,
    search_opportunities,
)
from app.mcp_server.tools.references import resolve_record_reference
from app.mcp_server.tools.relationships import get_relationships, recommend_recruiter
from app.mcp_server.tools.scheduling import list_scheduled_tasks, propose_scheduled_task
from app.mcp_server.tools.resume_drafts import (
    get_resume_draft,
    list_resume_drafts,
    propose_resume_section,
)
from app.mcp_server.tools.resumes import (
    analyse_role_target,
    get_resume,
    list_resumes,
    propose_resume_draft,
)
from app.mcp_server.tools.runs import get_recent_runs, get_run_items
from app.mcp_server.tools.status import get_ai_status, get_settings_summary
from app.mcp_server.tools.support import propose_create_github_issue
from app.mcp_server.tools.taxonomy_review import propose_taxonomy_bulk_review

__all__ = [
    "analyse_role_target",
    "check_manual_intake",
    "compare_records",
    "count_received_emails",
    "get_ai_status",
    "get_app_help",
    "get_candidate",
    "get_chart",
    "get_conversation",
    "get_draft_status",
    "get_label_thread_dossier",
    "get_metrics",
    "get_recent_runs",
    "get_relationships",
    "get_record_details",
    "get_resume",
    "get_resume_draft",
    "get_recruiter_replies",
    "get_run_items",
    "get_settings_summary",
    "get_tracked_application",
    "list_candidate_documents",
    "list_chat_attachments",
    "list_scheduled_tasks",
    "list_tracked_applications",
    "list_contact_numbers",
    "list_conversations",
    "list_external_opportunities",
    "list_gmail_labels",
    "list_label_threads",
    "list_recruiter_opportunities",
    "list_resume_drafts",
    "list_resumes",
    "navigate_to_queue",
    "propose_bulk_approve_candidates",
    "propose_add_note",
    "propose_candidate_action",
    "propose_create_github_issue",
    "propose_create_premium_contact",
    "propose_manual_requirement",
    "propose_nvoids_search",
    "propose_profile_update",
    "propose_record_update",
    "propose_track_record",
    "resolve_record_by_message_id",
    "propose_resume_draft",
    "propose_resume_section",
    "propose_scheduled_task",
    "propose_new_email",
    "propose_send_email",
    "propose_taxonomy_bulk_review",
    "rank_opportunities",
    "read_chat_attachment",
    "recommend_recruiter",
    "render_candidate_table",
    "resolve_record_reference",
    "search_candidates",
]
