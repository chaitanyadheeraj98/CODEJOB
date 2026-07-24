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
    email_columns = (
        sa.Column("source_parent_email_id", sa.Integer(), nullable=True),
        sa.Column("is_source_parent", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_multi_role_child", sa.Boolean(), nullable=False, server_default=sa.text("0")),
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
    for column in email_columns:
        op.add_column("recruiter_emails", column)
    op.create_index("ix_recruiter_emails_source_parent_email_id", "recruiter_emails", ["source_parent_email_id"])
    op.create_index("ix_recruiter_emails_sendability_status", "recruiter_emails", ["sendability_status"])
    op.create_index(
        "ux_recruiter_email_parent_requirement",
        "recruiter_emails",
        ["source_parent_email_id", "requirement_key"],
        unique=True,
    )

    op.add_column(
        "user_settings",
        sa.Column("feature_role_manifest_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "user_settings",
        sa.Column("candidate_work_authorizations_json", sa.Text(), nullable=False, server_default="[]"),
    )
    op.add_column("user_settings", sa.Column("candidate_total_experience_years", sa.Float(), nullable=True))
    op.add_column("user_settings", sa.Column("candidate_us_experience_years", sa.Float(), nullable=True))
    op.add_column(
        "user_settings",
        sa.Column("candidate_current_location", sa.String(length=255), nullable=False, server_default=""),
    )
    for name in ("source_count", "requirement_count", "multi_role_source_count", "manifest_review_count"):
        op.add_column("recent_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    for name in ("manifest_review_count", "multi_role_source_count", "requirement_count", "source_count"):
        op.drop_column("recent_runs", name)
    for name in (
        "candidate_current_location",
        "candidate_us_experience_years",
        "candidate_total_experience_years",
        "candidate_work_authorizations_json",
        "feature_role_manifest_enabled",
    ):
        op.drop_column("user_settings", name)

    op.drop_index("ux_recruiter_email_parent_requirement", table_name="recruiter_emails")
    op.drop_index("ix_recruiter_emails_sendability_status", table_name="recruiter_emails")
    op.drop_index("ix_recruiter_emails_source_parent_email_id", table_name="recruiter_emails")
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
        op.drop_column("recruiter_emails", name)
