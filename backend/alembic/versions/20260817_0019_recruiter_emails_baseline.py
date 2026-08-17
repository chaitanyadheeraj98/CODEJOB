"""Create the guarded recruiter_emails declarative baseline.

Revision ID: 20260817_0019
Revises: 20260817_0018
Create Date: 2026-08-17

This high-churn table is isolated from the smaller baseline revision for review
and rollback safety. Live-only legacy columns are preserved without adding ORM
mappings, so retired behavior is not accidentally re-enabled.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260817_0019"
down_revision = "20260817_0018"
branch_labels = None
depends_on = None


ColumnSpec = tuple[str, str, bool, bool, str | None]


def _c(
    name: str,
    type_name: str,
    nullable: bool = False,
    primary_key: bool = False,
    server_default: str | None = None,
) -> ColumnSpec:
    return name, type_name, nullable, primary_key, server_default


COLUMNS: tuple[ColumnSpec, ...] = (
    _c("id", "integer", primary_key=True),
    _c("owner_id", "string:100"),
    _c("sender", "string:255"),
    _c("subject", "string:500"),
    _c("body", "text"),
    _c("role", "string:255"),
    _c("location", "string:255"),
    _c("salary_text", "string:255"),
    _c("skills_text", "text"),
    _c("skills_json", "text", nullable=True),
    _c("score", "integer"),
    _c("decision", "string:50"),
    _c("state", "string:50"),
    _c("decision_reason", "text", nullable=True),
    _c("hard_filter_result", "text", nullable=True),
    _c("auto_reject_reason", "string:120", nullable=True),
    _c("ai_score", "float", nullable=True),
    _c("ai_score_source", "string:80", nullable=True),
    _c("ai_summary", "text", nullable=True),
    _c("ats_score", "float", nullable=True),
    _c("ats_score_source", "string:80", nullable=True),
    _c("ats_summary", "text", nullable=True),
    _c("ats_breakdown_json", "text", nullable=True),
    _c("resume_picker_score", "float", nullable=True),
    _c("resume_picker_reason", "text", nullable=True),
    _c("resume_picker_candidates_json", "text", nullable=True),
    _c("resume_picker_breakdown_json", "text", nullable=True),
    _c("semantic_input_source", "string:40", nullable=True),
    _c("semantic_input_chars", "integer", nullable=True),
    _c("semantic_chunks", "integer", nullable=True),
    _c("semantic_fallback_reason", "text", nullable=True),
    _c("keyword_source", "string:40", nullable=True),
    _c("thread_snapshot_used", "boolean", nullable=True),
    _c("thread_snapshot_email_id", "integer", nullable=True),
    _c("skip_reason", "string:120", nullable=True),
    _c("intent_type", "string:80", nullable=True),
    _c("intent_confidence", "float", nullable=True),
    _c("intent_reason", "text", nullable=True),
    _c("intent_evidence_json", "text", nullable=True),
    _c("intent_negative_evidence_json", "text", nullable=True),
    _c("gate_action", "string:40", nullable=True),
    _c("gate_provider", "string:80", nullable=True),
    _c("source_group_name", "string:255", nullable=True),
    _c("source_group_email", "string:255", nullable=True),
    _c("source_group_match_method", "string:80", nullable=True),
    _c("source_group_trusted", "boolean", nullable=True),
    _c("qualification_result", "string:80", nullable=True),
    _c("blocking_rule", "string:120", nullable=True),
    _c("qualification_detail", "text", nullable=True),
    _c("qualification_context_json", "text", nullable=True),
    _c("sync_batch_id", "string:100", nullable=True),
    _c("draft_reply", "text"),
    _c("draft_source", "string:50", nullable=True),
    _c("draft_model", "string:120", nullable=True),
    _c("draft_ai_error", "text", nullable=True),
    _c("draft_resume_context_status", "string:40", nullable=True),
    _c("semantic_embedding", "text", nullable=True),
    _c("approval_status", "string:50"),
    _c("sent_status", "string:50"),
    _c("source", "string:20"),
    _c("external_message_id", "string:255", nullable=True),
    _c("external_thread_id", "string:255", nullable=True),
    _c("external_rfc_message_id", "string:500", nullable=True),
    _c("gmail_received_at", "datetime", nullable=True),
    _c("applied_gmail_label", "string:120", nullable=True),
    _c("applied_gmail_label_id", "string:120", nullable=True),
    _c("applied_gmail_label_at", "datetime", nullable=True),
    _c("recipient_email", "string:255", nullable=True),
    _c("cc_email", "string:255", nullable=True),
    _c("routing_status", "string:50"),
    _c("routing_confidence", "float"),
    _c("routing_reason", "text"),
    _c("routing_evidence", "text"),
    _c("routing_candidates", "text"),
    _c("routing_confirmed", "boolean"),
    _c("resume_asset_id", "integer", nullable=True),
    _c("resume_file_name", "string:255", nullable=True),
    _c("parser_details_json", "text", nullable=True),
    _c("company", "string:255", nullable=True),
    _c("end_client", "string:255", nullable=True),
    _c("implementation_partner", "string:255", nullable=True),
    _c("domain", "string:120", nullable=True),
    _c("domain_confidence", "string:20", nullable=True),
    _c("interview_type", "string:255", nullable=True),
    _c("screening_mode", "string:30", nullable=True),
    _c("source_parent_email_id", "integer", nullable=True),
    _c("is_source_parent", "boolean"),
    _c("is_multi_role_child", "boolean"),
    _c("requirement_index", "integer", nullable=True),
    _c("requirement_count", "integer", nullable=True),
    _c("requirement_key", "string:64", nullable=True),
    _c("requirement_source_text", "text", nullable=True),
    _c("inherited_constraints_json", "text", nullable=True),
    _c("role_manifest_status", "string:40"),
    _c("role_manifest_confidence", "float", nullable=True),
    _c("role_manifest_json", "text", nullable=True),
    _c("role_manifest_diagnostics_json", "text", nullable=True),
    _c("eligibility_status", "string:40", nullable=True),
    _c("eligibility_details_json", "text", nullable=True),
    _c("sendability_status", "string:50", nullable=True),
    _c("sent_at", "datetime", nullable=True),
    _c("gmail_sent_id", "string:255", nullable=True),
    _c("tracking_token", "string:64", nullable=True),
    _c("opened_at", "datetime", nullable=True),
    _c("open_count", "integer"),
    _c("sent_attachment_file_names_json", "text", nullable=True),
    _c("last_error", "text", nullable=True),
    _c("created_at", "datetime"),
    _c("updated_at", "datetime"),
    # Preserved live-only legacy columns.
    _c("is_premium", "boolean", nullable=True),
    _c("recruiter_phone", "string:80", nullable=True),
    _c("recruiter_phone_confidence", "float", nullable=True),
    _c("recruiter_phone_reason", "text", nullable=True),
    _c("employer_phone", "string:80", nullable=True),
    _c("employer_phone_confidence", "float", nullable=True),
    _c("cold_call_script", "text", nullable=True),
    _c("cold_call_script_source", "string:50", nullable=True),
    _c("cold_call_script_error", "text", nullable=True),
    _c("message_intent", "string:50", nullable=True),
    _c("intent_evidence", "text", nullable=True),
    _c("jd_profile_json", "text", nullable=True),
    _c("jd_embedding", "text", nullable=True),
    _c("resume_match_score", "float", nullable=True),
    _c("hard_requirement_score", "float", nullable=True),
    _c("technical_stack_score", "float", nullable=True),
    _c("responsibility_alignment_score", "float", nullable=True),
    _c("evidence_summary_json", "text", server_default="'{}'"),
    _c("missing_requirements_json", "text", server_default="'[]'"),
    _c("risk_flags_json", "text", server_default="'[]'"),
    _c("match_recommendation", "string:80", nullable=True),
    _c("resume_match_source", "string:80", nullable=True),
    _c("resume_match_version", "string:40", nullable=True),
)


INDEXES: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("ix_recruiter_emails_id", ("id",), False),
    ("ix_recruiter_emails_owner_id", ("owner_id",), False),
    ("ix_recruiter_emails_sender", ("sender",), False),
    ("ix_recruiter_emails_decision", ("decision",), False),
    ("ix_recruiter_emails_state", ("state",), False),
    ("ix_recruiter_emails_sync_batch_id", ("sync_batch_id",), False),
    ("ix_recruiter_emails_external_message_id", ("external_message_id",), True),
    ("ix_recruiter_emails_company", ("company",), False),
    ("ix_recruiter_emails_end_client", ("end_client",), False),
    ("ix_recruiter_emails_implementation_partner", ("implementation_partner",), False),
    ("ix_recruiter_emails_domain", ("domain",), False),
    ("ix_recruiter_emails_interview_type", ("interview_type",), False),
    ("ix_recruiter_emails_source_parent_email_id", ("source_parent_email_id",), False),
    ("ix_recruiter_emails_sendability_status", ("sendability_status",), False),
    ("ix_recruiter_emails_tracking_token", ("tracking_token",), True),
)


def _type(type_name: str) -> sa.types.TypeEngine:
    if type_name == "integer":
        return sa.Integer()
    if type_name == "float":
        return sa.Float()
    if type_name == "boolean":
        return sa.Boolean()
    if type_name == "text":
        return sa.Text()
    if type_name == "datetime":
        return sa.DateTime()
    if type_name.startswith("string:"):
        return sa.String(length=int(type_name.split(":", 1)[1]))
    raise ValueError(f"Unsupported baseline type: {type_name}")


def _column(spec: ColumnSpec) -> sa.Column:
    name, type_name, nullable, primary_key, server_default = spec
    default = None if server_default is None else sa.text(server_default)
    return sa.Column(
        name,
        _type(type_name),
        nullable=nullable,
        primary_key=primary_key,
        server_default=default,
    )


def upgrade() -> None:
    if "recruiter_emails" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "recruiter_emails",
        *(_column(spec) for spec in COLUMNS),
        sa.UniqueConstraint(
            "source_parent_email_id",
            "requirement_key",
            name="ux_recruiter_email_parent_requirement",
        ),
    )
    for index_name, columns, unique in INDEXES:
        op.create_index(index_name, "recruiter_emails", list(columns), unique=unique)


def downgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    if bind.execute(sa.text('SELECT 1 FROM "recruiter_emails" LIMIT 1')).first() is not None:
        raise RuntimeError(
            "Refusing destructive recruiter_emails baseline downgrade while the table contains data"
        )
    op.drop_table("recruiter_emails")
