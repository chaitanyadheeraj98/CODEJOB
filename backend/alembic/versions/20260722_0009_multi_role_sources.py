"""Add multi-role source, eligibility, and sendability state.

Revision ID: 20260722_0009
Revises: 20260713_0008
Create Date: 2026-07-22 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260722_0009"
down_revision = "20260713_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    email_columns = (
        sa.Column("source_parent_email_id", sa.Integer(), nullable=True),
        sa.Column("is_source_parent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_multi_role_child", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requirement_index", sa.Integer(), nullable=True),
        sa.Column("requirement_count", sa.Integer(), nullable=True),
        sa.Column("requirement_key", sa.String(length=64), nullable=True),
        sa.Column("requirement_source_text", sa.Text(), nullable=True),
        sa.Column("inherited_constraints_json", sa.Text(), nullable=True),
        sa.Column("role_manifest_status", sa.String(length=40), nullable=False, server_default="not_run"),
        sa.Column("role_manifest_confidence", sa.Float(), nullable=True),
        sa.Column("role_manifest_json", sa.Text(), nullable=True),
        sa.Column("role_manifest_diagnostics_json", sa.Text(), nullable=True),
        sa.Column("eligibility_status", sa.String(length=40), nullable=True),
        sa.Column("eligibility_details_json", sa.Text(), nullable=True),
        sa.Column("sendability_status", sa.String(length=50), nullable=True),
    )
    if "recruiter_emails" in tables:
        email_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")
        }
        for column in email_columns:
            if column.name not in email_existing:
                op.add_column("recruiter_emails", column.copy())
        email_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("recruiter_emails")
        }
        if "ix_recruiter_emails_source_parent_email_id" not in email_indexes:
            op.create_index(
                "ix_recruiter_emails_source_parent_email_id",
                "recruiter_emails",
                ["source_parent_email_id"],
            )
        if "ix_recruiter_emails_sendability_status" not in email_indexes:
            op.create_index(
                "ix_recruiter_emails_sendability_status",
                "recruiter_emails",
                ["sendability_status"],
            )
        if "ux_recruiter_email_parent_requirement" not in email_indexes:
            op.create_index(
                "ux_recruiter_email_parent_requirement",
                "recruiter_emails",
                ["source_parent_email_id", "requirement_key"],
                unique=True,
            )

    if "user_settings" in tables:
        settings_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("user_settings")
        }
        settings_columns = (
            sa.Column(
                "feature_role_manifest_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "candidate_work_authorizations_json",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
            sa.Column("candidate_total_experience_years", sa.Float(), nullable=True),
            sa.Column("candidate_us_experience_years", sa.Float(), nullable=True),
            sa.Column(
                "candidate_current_location",
                sa.String(length=255),
                nullable=False,
                server_default="",
            ),
        )
        for column in settings_columns:
            if column.name not in settings_existing:
                op.add_column("user_settings", column.copy())

    if "recent_runs" in tables:
        recent_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("recent_runs")
        }
        for name in (
            "source_count",
            "requirement_count",
            "multi_role_source_count",
            "manifest_review_count",
        ):
            if name not in recent_existing:
                op.add_column(
                    "recent_runs",
                    sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
                )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "recent_runs" in tables:
        recent_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("recent_runs")
        }
        for name in (
            "manifest_review_count",
            "multi_role_source_count",
            "requirement_count",
            "source_count",
        ):
            if name in recent_existing:
                op.drop_column("recent_runs", name)
    if "user_settings" in tables:
        settings_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("user_settings")
        }
        for name in (
            "candidate_current_location",
            "candidate_us_experience_years",
            "candidate_total_experience_years",
            "candidate_work_authorizations_json",
            "feature_role_manifest_enabled",
        ):
            if name in settings_existing:
                op.drop_column("user_settings", name)

    if "recruiter_emails" in tables:
        email_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("recruiter_emails")
        }
        for index_name in (
            "ux_recruiter_email_parent_requirement",
            "ix_recruiter_emails_sendability_status",
            "ix_recruiter_emails_source_parent_email_id",
        ):
            if index_name in email_indexes:
                op.drop_index(index_name, table_name="recruiter_emails")
        email_existing = {
            column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")
        }
        for name in (
            "sendability_status",
            "eligibility_details_json",
            "eligibility_status",
            "role_manifest_diagnostics_json",
            "role_manifest_json",
            "role_manifest_confidence",
            "role_manifest_status",
            "inherited_constraints_json",
            "requirement_source_text",
            "requirement_key",
            "requirement_count",
            "requirement_index",
            "is_multi_role_child",
            "is_source_parent",
            "source_parent_email_id",
        ):
            if name in email_existing:
                op.drop_column("recruiter_emails", name)
