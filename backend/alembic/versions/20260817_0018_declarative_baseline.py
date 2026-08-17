"""Create the declarative baseline tables missing from historical migrations.

Revision ID: 20260817_0018
Revises: 20260815_0016
Create Date: 2026-08-17

The legacy ``candidate_resume_matches`` table is intentionally preserved. It
has no current ORM/service owner and was empty when audited, but reproducing it
is safer than silently dropping an unknown schema object during consolidation.

Columns marked as legacy below were found by read-only introspection of the
live SQLite volume. They remain outside the ORM on purpose so this migration
does not resurrect retired application behavior.
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
import sqlalchemy as sa


revision = "20260817_0018"
down_revision = "20260815_0016"
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


TABLES: dict[str, tuple[ColumnSpec, ...]] = {
    "user_settings": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("enabled", "boolean"),
        _c("gmail_query", "text"),
        _c("default_gmail_query", "text"),
        _c("saved_gmail_queries_json", "text"),
        _c("mail_date", "string:10", nullable=True),
        _c("default_date_mode", "string:20"),
        _c("min_salary", "integer", nullable=True),
        _c("accepted_locations", "text"),
        _c("visa_required_allowed", "boolean"),
        _c("remote_preference", "string:50"),
        _c("role_keywords", "text"),
        _c("must_have_skills", "text"),
        _c("employer_domains", "text"),
        _c("free_text_guidance", "text"),
        _c("qualification_threshold", "float"),
        _c("feature_auto_polling", "boolean"),
        _c("feature_auto_poll_interval_minutes", "integer"),
        _c("feature_nvoids_enabled", "boolean"),
        _c("feature_nvoids_auto_sync", "boolean"),
        _c("feature_nvoids_poll_interval_minutes", "integer"),
        _c("nvoids_batch_limit", "integer"),
        _c("nvoids_detail_title_mode", "string:40"),
        _c("nvoids_locations", "text"),
        _c("feature_auto_send", "boolean"),
        _c("feature_retry_queue", "boolean"),
        _c("feature_ai_enabled", "boolean"),
        _c("feature_ai_extractor_enabled", "boolean"),
        _c("feature_semantic_enabled", "boolean"),
        _c("feature_groq_job_parser_enabled", "boolean"),
        _c("feature_gmail_requirement_groups_enabled", "boolean"),
        _c("feature_role_manifest_enabled", "boolean"),
        _c("feature_strict_candidate_screening_enabled", "boolean"),
        _c("feature_email_tracking_enabled", "boolean"),
        _c("feature_reply_inbox_enabled", "boolean"),
        _c("candidate_work_authorizations_json", "text"),
        _c("candidate_total_experience_years", "float", nullable=True),
        _c("candidate_us_experience_years", "float", nullable=True),
        _c("candidate_current_location", "string:255"),
        _c("draft_text_size", "string:20"),
        _c("fallback_draft_template", "text"),
        _c("signature_name", "string:255"),
        _c("signature_phone", "string:80"),
        _c("signature_email", "string:255"),
        _c("preferred_employer_cc_email", "string:255"),
        _c("preferred_employer_cc_emails", "text"),
        _c("default_employer_cc_emails", "text"),
        _c("resume_display_name", "string:255"),
        _c("policy_json", "text"),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
        # Preserved live-only legacy column. The default keeps ORM inserts valid.
        _c("feature_resume_matching_enabled", "boolean", server_default="false"),
    ),
    "resume_assets": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("file_path", "text"),
        _c("file_name", "string:255"),
        _c("mime_type", "string:120"),
        _c("sha256", "string:64"),
        _c("version", "integer"),
        _c("skills_text", "text"),
        _c("is_enabled", "boolean"),
        _c("is_current", "boolean"),
        _c("semantic_embedding", "text", nullable=True),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
        # Preserved live-only legacy columns.
        _c("display_name", "string:255", nullable=True),
        _c("normalized_skills_json", "text", server_default="'[]'"),
        _c("skills_embedding", "text", nullable=True),
        _c("embedding_provider", "string:80", nullable=True),
        _c("embedding_model", "string:120", nullable=True),
        _c("skills_extraction_status", "string:40", server_default="'pending'"),
        _c("evidence_profile_json", "text", server_default="'{}'"),
        _c("evidence_extraction_status", "string:40", server_default="'pending'"),
        _c("profile_version", "string:40", server_default="'resume_match_v1'"),
        _c("source_content_hash", "string:64", nullable=True),
    ),
    "attachment_assets": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("file_path", "text"),
        _c("file_name", "string:255"),
        _c("mime_type", "string:120"),
        _c("sha256", "string:64"),
        _c("file_size", "integer"),
        _c("is_enabled", "boolean"),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "sync_runs": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("sync_batch_id", "string:100"),
        _c("started_at", "datetime"),
        _c("ended_at", "datetime", nullable=True),
        _c("imported_count", "integer"),
        _c("skipped_count", "integer"),
        _c("error_count", "integer"),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "draft_edit_feedback": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("recruiter_email_id", "integer", nullable=True),
        _c("original_draft", "text"),
        _c("edited_draft", "text"),
        _c("created_at", "datetime"),
    ),
    "recipient_routing_feedback": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("sender_domain", "string:255"),
        _c("corrected_to", "string:255"),
        _c("corrected_cc", "string:255"),
        _c("sample_sender", "string:255", nullable=True),
        _c("evidence_to_present", "boolean"),
        _c("evidence_cc_present", "boolean"),
        _c("sample_body", "text", nullable=True),
        _c("created_at", "datetime"),
    ),
    "premium_number_leads": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("recruiter_email_id", "integer"),
        _c("phone_number_normalized", "string:40"),
        _c("phone_number_display", "string:80"),
        _c("owner_name", "string:255"),
        _c("company", "string:255"),
        _c("designation", "string:255"),
        _c("purpose", "string:255"),
        _c("confidence", "string:10"),
        _c("contact_type", "string:40"),
        _c("recruiter_relevance_score", "integer"),
        _c("is_recruiter_relevant", "boolean"),
        _c("relevance_reason", "string:255"),
        _c("source_fragment", "text"),
        _c("source_email_sender", "string:255"),
        _c("source_email_subject", "string:500"),
        _c("source_email_message_id", "string:255", nullable=True),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "recruiter_numbers": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("normalized_phone_number", "string:40"),
        _c("display_phone_number", "string:80"),
        _c("recruiter_name", "string:255"),
        _c("company", "string:255"),
        _c("designation", "string:255"),
        _c("recruiter_email", "string:255"),
        _c("first_detected_email_id", "integer", nullable=True),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "employer_numbers": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("normalized_phone_number", "string:40"),
        _c("display_phone_number", "string:80"),
        _c("owner_name", "string:255"),
        _c("company", "string:255"),
        _c("source_email_id", "integer", nullable=True),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "recruiter_opportunities": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("recruiter_number_id", "integer"),
        _c("source_email_id", "integer", nullable=True),
        _c("gmail_message_id", "string:255"),
        _c("source_type", "string:40"),
        _c("source_url", "string:1200", nullable=True),
        _c("external_opportunity_id", "integer", nullable=True),
        _c("email_subject", "string:500"),
        _c("email_sender", "string:255"),
        _c("gmail_open_url", "string:1000"),
        _c("received_at", "datetime", nullable=True),
        _c("job_title", "string:255"),
        _c("client", "string:255"),
        _c("location", "string:255"),
        _c("work_mode", "string:80"),
        _c("visa_restrictions", "string:255"),
        _c("extracted_skills", "text"),
        _c("evidence", "text"),
        _c("status", "string:40"),
        _c("notes", "text"),
        _c("cold_call_script", "text", nullable=True),
        _c("cold_call_script_updated_at", "datetime", nullable=True),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "number_review_queue": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("source_email_id", "integer"),
        _c("normalized_phone_number", "string:40"),
        _c("display_phone_number", "string:80"),
        _c("owner_name", "string:255"),
        _c("company", "string:255"),
        _c("designation", "string:255"),
        _c("confidence", "string:10"),
        _c("purpose", "string:255"),
        _c("evidence_snippet", "text"),
        _c("email_subject", "string:500"),
        _c("email_sender", "string:255"),
        _c("gmail_open_url", "string:1000"),
        _c("state", "string:40"),
        _c("created_at", "datetime"),
        _c("updated_at", "datetime"),
    ),
    "productivity_events": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("event_type", "string:80"),
        _c("event_source", "string:40"),
        _c("entity_id", "integer", nullable=True),
        _c("weight", "float"),
        _c("metadata_json", "text"),
        _c("occurred_at", "datetime"),
        _c("created_at", "datetime"),
    ),
    "candidate_resume_matches": (
        _c("id", "integer", primary_key=True),
        _c("owner_id", "string:100"),
        _c("candidate_id", "integer"),
        _c("resume_asset_id", "integer"),
        _c("overall_score", "float"),
        _c("hard_requirement_score", "float"),
        _c("technical_score", "float"),
        _c("semantic_score", "float"),
        _c("matched_requirements_json", "text"),
        _c("missing_requirements_json", "text"),
        _c("evidence_json", "text"),
        _c("risk_flags_json", "text"),
        _c("is_selected", "boolean"),
        _c("created_at", "datetime"),
    ),
}


INDEXES: dict[str, tuple[tuple[str, tuple[str, ...], bool], ...]] = {
    "user_settings": (
        ("ix_user_settings_id", ("id",), False),
        ("ix_user_settings_owner_id", ("owner_id",), True),
    ),
    "resume_assets": (
        ("ix_resume_assets_id", ("id",), False),
        ("ix_resume_assets_owner_id", ("owner_id",), False),
        ("ix_resume_assets_sha256", ("sha256",), False),
    ),
    "attachment_assets": (
        ("ix_attachment_assets_id", ("id",), False),
        ("ix_attachment_assets_owner_id", ("owner_id",), False),
        ("ix_attachment_assets_sha256", ("sha256",), False),
    ),
    "sync_runs": (
        ("ix_sync_runs_id", ("id",), False),
        ("ix_sync_runs_owner_id", ("owner_id",), False),
        ("ix_sync_runs_sync_batch_id", ("sync_batch_id",), True),
    ),
    "draft_edit_feedback": (
        ("ix_draft_edit_feedback_id", ("id",), False),
        ("ix_draft_edit_feedback_owner_id", ("owner_id",), False),
    ),
    "recipient_routing_feedback": (
        ("ix_recipient_routing_feedback_id", ("id",), False),
        ("ix_recipient_routing_feedback_owner_id", ("owner_id",), False),
        ("ix_recipient_routing_feedback_sender_domain", ("sender_domain",), False),
    ),
    "premium_number_leads": (
        ("ix_premium_number_leads_id", ("id",), False),
        ("ix_premium_number_leads_owner_id", ("owner_id",), False),
        ("ix_premium_number_leads_recruiter_email_id", ("recruiter_email_id",), False),
        ("ix_premium_number_leads_phone_number_normalized", ("phone_number_normalized",), False),
    ),
    "recruiter_numbers": (
        ("ix_recruiter_numbers_id", ("id",), False),
        ("ix_recruiter_numbers_owner_id", ("owner_id",), False),
        ("ix_recruiter_numbers_normalized_phone_number", ("normalized_phone_number",), False),
    ),
    "employer_numbers": (
        ("ix_employer_numbers_id", ("id",), False),
        ("ix_employer_numbers_owner_id", ("owner_id",), False),
        ("ix_employer_numbers_normalized_phone_number", ("normalized_phone_number",), False),
    ),
    "recruiter_opportunities": (
        ("ix_recruiter_opportunities_id", ("id",), False),
        ("ix_recruiter_opportunities_owner_id", ("owner_id",), False),
        ("ix_recruiter_opportunities_recruiter_number_id", ("recruiter_number_id",), False),
        ("ix_recruiter_opportunities_gmail_message_id", ("gmail_message_id",), False),
        ("ix_recruiter_opportunities_source_type", ("source_type",), False),
        ("ix_recruiter_opportunities_external_opportunity_id", ("external_opportunity_id",), False),
    ),
    "number_review_queue": (
        ("ix_number_review_queue_id", ("id",), False),
        ("ix_number_review_queue_owner_id", ("owner_id",), False),
        ("ix_number_review_queue_source_email_id", ("source_email_id",), False),
        ("ix_number_review_queue_normalized_phone_number", ("normalized_phone_number",), False),
    ),
    "productivity_events": (
        ("ix_productivity_events_id", ("id",), False),
        ("ix_productivity_events_owner_id", ("owner_id",), False),
        ("ix_productivity_events_event_type", ("event_type",), False),
        ("ix_productivity_events_occurred_at", ("occurred_at",), False),
    ),
    "candidate_resume_matches": (
        ("ix_candidate_resume_matches_id", ("id",), False),
        ("ix_candidate_resume_matches_owner_id", ("owner_id",), False),
        ("ix_candidate_resume_matches_candidate_id", ("candidate_id",), False),
        ("ix_candidate_resume_matches_resume_asset_id", ("resume_asset_id",), False),
    ),
}


UNIQUES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "recruiter_numbers": (
        ("ux_recruiter_numbers_owner_phone", ("owner_id", "normalized_phone_number")),
    ),
    "employer_numbers": (
        ("ux_employer_numbers_owner_phone", ("owner_id", "normalized_phone_number")),
    ),
    "recruiter_opportunities": (
        (
            "ux_recruiter_opportunities_owner_recruiter_msg",
            ("owner_id", "recruiter_number_id", "gmail_message_id"),
        ),
    ),
    "number_review_queue": (
        (
            "ux_number_review_queue_owner_phone_email",
            ("owner_id", "normalized_phone_number", "source_email_id"),
        ),
    ),
}


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
    default: sa.ClauseElement | None
    if server_default == "false":
        default = sa.false()
    elif server_default is None:
        default = None
    else:
        default = sa.text(server_default)
    return sa.Column(
        name,
        _type(type_name),
        nullable=nullable,
        primary_key=primary_key,
        server_default=default,
    )


def _table_args(table_name: str) -> Iterable[sa.UniqueConstraint]:
    return (
        sa.UniqueConstraint(*columns, name=name)
        for name, columns in UNIQUES.get(table_name, ())
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    for table_name, specs in TABLES.items():
        if table_name in existing:
            continue
        op.create_table(
            table_name,
            *(_column(spec) for spec in specs),
            *_table_args(table_name),
        )
        for index_name, columns, unique in INDEXES.get(table_name, ()):
            op.create_index(index_name, table_name, list(columns), unique=unique)

    # These audit fields were historically added only by db.py startup SQL.
    if "recent_run_skipped_items" in existing:
        skipped_columns = {
            column["name"]
            for column in sa.inspect(bind).get_columns("recent_run_skipped_items")
        }
        additions = (
            _c("intent_type", "string:80", nullable=True),
            _c("intent_confidence", "float", nullable=True),
            _c("intent_reason", "text", nullable=True),
            _c("intent_evidence_json", "text", nullable=True),
            _c("intent_negative_evidence_json", "text", nullable=True),
            _c("gate_action", "string:40", nullable=True),
            _c("gate_provider", "string:80", nullable=True),
        )
        for spec in additions:
            if spec[0] not in skipped_columns:
                op.add_column("recent_run_skipped_items", _column(spec))


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())
    nonempty = [
        table_name
        for table_name in TABLES
        if table_name in existing
        and bind.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1')).first() is not None
    ]
    if nonempty:
        raise RuntimeError(
            "Refusing destructive declarative-baseline downgrade; non-empty tables: "
            + ", ".join(sorted(nonempty))
        )
    for table_name in reversed(tuple(TABLES)):
        if table_name in existing:
            op.drop_table(table_name)
