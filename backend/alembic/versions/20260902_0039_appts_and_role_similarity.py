"""Add explicit AppTS storage and recruiter following metadata.

Revision ID: 20260902_0039
Revises: 20260901_0038
"""
from alembic import op
import sqlalchemy as sa

revision = "20260902_0039"
down_revision = "20260901_0038"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index.get("name")}


def _add(table: str, column: sa.Column) -> None:
    if column.name not in _columns(table):
        op.add_column(table, column)


def _index(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def _application_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(100), nullable=False, server_default="default-owner"),
        sa.Column("resume_asset_id", sa.Integer(), nullable=False),
        sa.Column("resume_version_snapshot", sa.Integer(), nullable=False),
        sa.Column("resume_file_name_snapshot", sa.String(255), nullable=False),
        sa.Column("resume_sha256_snapshot", sa.String(64), nullable=False),
        sa.Column("recruiter_opportunity_id", sa.Integer(), nullable=True),
        sa.Column("recruiter_contact_id", sa.Integer(), nullable=True),
        sa.Column("recruiter_name_snapshot", sa.String(255), nullable=False, server_default=""),
        sa.Column("recruiter_company_snapshot", sa.String(255), nullable=False, server_default=""),
        sa.Column("job_title_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("end_client_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("location_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_recruiter_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("manual_recruiter_company", sa.String(255), nullable=False, server_default=""),
        sa.Column("manual_recruiter_email", sa.String(255), nullable=False, server_default=""),
        sa.Column("manual_recruiter_phone", sa.String(80), nullable=False, server_default=""),
        sa.Column("manual_recruiter_linkedin_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_job_title", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_end_client", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_jd_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("manual_source_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("resume_submission_status", sa.String(30), nullable=False, server_default="not_submitted"),
        sa.Column("resume_submitted_at", sa.DateTime(), nullable=True),
        sa.Column("submission_method", sa.String(20), nullable=False, server_default="email"),
        sa.Column("rejection_detail_tags_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("dedupe_key", sa.String(64), nullable=True),
        sa.Column("resume_skills_snapshot_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("resume_primary_role_snapshot", sa.String(255), nullable=False, server_default=""),
        sa.Column("milestones_reached_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(40), nullable=False, server_default="matched"),
        sa.Column("status_changed_at", sa.DateTime(), nullable=False),
        sa.Column("resume_shared_at", sa.DateTime(), nullable=True),
        sa.Column("submitted_to_client_at", sa.DateTime(), nullable=True),
        sa.Column("next_action_type", sa.String(80), nullable=True),
        sa.Column("next_action_at", sa.DateTime(), nullable=True),
        sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_contact_at", sa.DateTime(), nullable=True),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("closed_reason", sa.String(120), nullable=True),
        sa.Column("closed_reason_code", sa.String(40), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("embedding_model", sa.String(255), nullable=True),
        sa.Column("resolved_recruiter_contact_id", sa.Integer(), nullable=True),
        sa.Column("resolved_recruiter_email", sa.String(255), nullable=True),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("owner_id", "dedupe_key", name="ux_appts_applications_owner_dedupe_key"),
    ]


def upgrade() -> None:
    for column in (
        sa.Column("marked_for_tracking", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("resolved_recruiter_contact_id", sa.Integer(), nullable=True),
        sa.Column("resolved_recruiter_email", sa.String(255), nullable=True),
        sa.Column("embedding_model", sa.String(255), nullable=True),
    ):
        _add("recruiter_emails", column)
    for name, columns in (
        ("ix_recruiter_emails_marked_for_tracking", ["marked_for_tracking"]),
        ("ix_recruiter_emails_resolved_recruiter_contact_id", ["resolved_recruiter_contact_id"]),
        ("ix_recruiter_emails_resolved_recruiter_email", ["resolved_recruiter_email"]),
    ):
        _index(name, "recruiter_emails", columns)

    tables = _tables()
    if "appts_applications" not in tables:
        op.create_table("appts_applications", *_application_columns())
    for name, columns in (
        ("ix_appts_applications_owner_id", ["owner_id"]),
        ("ix_appts_applications_status", ["status"]),
        ("ix_appts_applications_resume_asset_id", ["resume_asset_id"]),
        ("ix_appts_applications_resolved_contact", ["resolved_recruiter_contact_id"]),
        ("ix_appts_applications_resolved_email", ["resolved_recruiter_email"]),
        ("ix_appts_applications_deleted_at", ["deleted_at"]),
    ):
        _index(name, "appts_applications", columns)

    side_tables = {
        "appts_application_events": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False),
            sa.Column("event_type", sa.String(80), nullable=False), sa.Column("event_source", sa.String(40), nullable=False, server_default="user"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""), sa.Column("linked_recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        ],
        "appts_application_rtrs": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False), sa.Column("status", sa.String(20), nullable=False, server_default="requested"),
            sa.Column("role_scope", sa.Text(), nullable=False, server_default=""), sa.Column("end_client_scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("requested_at", sa.DateTime(), nullable=False), sa.Column("confirmed_at", sa.DateTime(), nullable=True), sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("proof_attachment_id", sa.Integer(), nullable=True), sa.Column("proof_recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        ],
        "appts_application_interviews": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False), sa.Column("round_type", sa.String(40), nullable=False, server_default="interview_1"),
            sa.Column("scheduled_at", sa.DateTime(), nullable=True), sa.Column("format", sa.String(40), nullable=False, server_default=""),
            sa.Column("interviewer_names", sa.Text(), nullable=False, server_default=""), sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
            sa.Column("result", sa.String(20), nullable=False, server_default="scheduled"), sa.Column("follow_up_task_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("deleted_at", sa.DateTime(), nullable=True), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False),
        ],
        "appts_application_suggestions": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False), sa.Column("suggestion_type", sa.String(20), nullable=False),
            sa.Column("status", sa.String(20), nullable=False, server_default="pending"), sa.Column("confidence", sa.String(10), nullable=False, server_default="high"),
            sa.Column("reply_message_id", sa.Integer(), nullable=True), sa.Column("recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("suggested_status", sa.String(40), nullable=True), sa.Column("suggested_next_action_type", sa.String(80), nullable=True),
            sa.Column("suggested_next_action_at", sa.DateTime(), nullable=True), sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"), sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("resolved_at", sa.DateTime(), nullable=True),
        ],
        "appts_application_skill_gap_snapshots": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False), sa.Column("source", sa.String(20), nullable=False, server_default="fallback_text"),
            sa.Column("matched_required_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("missing_required_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("matched_preferred_json", sa.Text(), nullable=False, server_default="[]"), sa.Column("missing_preferred_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("computed_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("owner_id", "application_id", name="ux_appts_skill_gap_snapshot_application"),
        ],
        "appts_application_outreach_messages": [
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("application_id", sa.Integer(), sa.ForeignKey("appts_applications.id"), nullable=False), sa.Column("message_kind", sa.String(40), nullable=False),
            sa.Column("draft_source", sa.String(20), nullable=False, server_default="unknown"), sa.Column("ai_model", sa.String(80), nullable=True),
            sa.Column("subject", sa.Text(), nullable=False, server_default=""), sa.Column("body", sa.Text(), nullable=False, server_default=""), sa.Column("sent_at", sa.DateTime(), nullable=False),
        ],
    }
    for table, columns in side_tables.items():
        if table not in _tables():
            op.create_table(table, *columns)
        _index(f"ix_{table}_owner_id", table, ["owner_id"])
        _index(f"ix_{table}_application_id", table, ["application_id"])

    if "role_similarity_checks" not in _tables():
        op.create_table(
            "role_similarity_checks", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("left_record_type", sa.String(40), nullable=False), sa.Column("left_record_id", sa.Integer(), nullable=False),
            sa.Column("right_record_type", sa.String(40), nullable=False), sa.Column("right_record_id", sa.Integer(), nullable=False),
            sa.Column("skill_overlap_score", sa.Float(), nullable=True), sa.Column("embedding_score", sa.Float(), nullable=True),
            sa.Column("final_score", sa.Float(), nullable=False), sa.Column("tier", sa.String(20), nullable=False),
            sa.Column("method", sa.String(20), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _index("ix_role_similarity_checks_owner_id", "role_similarity_checks", ["owner_id"])


def downgrade() -> None:
    for table in (
        "role_similarity_checks", "appts_application_outreach_messages", "appts_application_skill_gap_snapshots",
        "appts_application_suggestions", "appts_application_interviews", "appts_application_rtrs", "appts_application_events", "appts_applications",
    ):
        if table in _tables():
            op.drop_table(table)
    for index_name, column in (
        ("ix_recruiter_emails_resolved_recruiter_email", "resolved_recruiter_email"),
        ("ix_recruiter_emails_resolved_recruiter_contact_id", "resolved_recruiter_contact_id"),
        ("ix_recruiter_emails_marked_for_tracking", "marked_for_tracking"),
    ):
        if index_name in _indexes("recruiter_emails"):
            op.drop_index(index_name, table_name="recruiter_emails")
        if column in _columns("recruiter_emails"):
            op.drop_column("recruiter_emails", column)
    if "embedding_model" in _columns("recruiter_emails"):
        op.drop_column("recruiter_emails", "embedding_model")
