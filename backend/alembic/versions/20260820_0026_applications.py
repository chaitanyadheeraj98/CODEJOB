"""Add applications, application events, and the applications feature flag.

Revision ID: 20260820_0026
Revises: 20260820_0025
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260820_0026"
down_revision = "20260820_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_settings" in tables:
        columns = {column["name"] for column in inspector.get_columns("user_settings")}
        if "feature_applications_enabled" not in columns:
            op.add_column(
                "user_settings",
                sa.Column(
                    "feature_applications_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )

    if "applications" not in tables:
        op.create_table(
            "applications",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("resume_asset_id", sa.Integer(), nullable=False),
            sa.Column("resume_version_snapshot", sa.Integer(), nullable=False),
            sa.Column("resume_file_name_snapshot", sa.String(length=255), nullable=False),
            sa.Column("resume_sha256_snapshot", sa.String(length=64), nullable=False),
            sa.Column("recruiter_opportunity_id", sa.Integer(), nullable=False),
            sa.Column("recruiter_contact_id", sa.Integer(), nullable=False),
            sa.Column("recruiter_name_snapshot", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("recruiter_company_snapshot", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("job_title_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("end_client_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="matched"),
            sa.Column("status_changed_at", sa.DateTime(), nullable=False),
            sa.Column("resume_shared_at", sa.DateTime(), nullable=True),
            sa.Column("submitted_to_client_at", sa.DateTime(), nullable=True),
            sa.Column("next_action_type", sa.String(length=80), nullable=True),
            sa.Column("next_action_at", sa.DateTime(), nullable=True),
            sa.Column("follow_up_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_contact_at", sa.DateTime(), nullable=True),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("closed_reason", sa.String(length=120), nullable=True),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_applications_owner_id", "applications", ["owner_id"])
        op.create_index("ix_applications_resume_asset_id", "applications", ["resume_asset_id"])
        op.create_index(
            "ix_applications_recruiter_opportunity_id",
            "applications",
            ["recruiter_opportunity_id"],
        )
        op.create_index("ix_applications_recruiter_contact_id", "applications", ["recruiter_contact_id"])
        op.create_index("ix_applications_status", "applications", ["status"])
        op.create_index("ix_applications_next_action_at", "applications", ["next_action_at"])
        op.create_index("ix_applications_deleted_at", "applications", ["deleted_at"])
        op.create_index(
            "ux_applications_owner_resume_opportunity",
            "applications",
            ["owner_id", "resume_asset_id", "recruiter_opportunity_id"],
            unique=True,
        )

    if "application_events" not in tables:
        op.create_table(
            "application_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("application_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("event_source", sa.String(length=40), nullable=False, server_default="user"),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("linked_recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_application_events_owner_id", "application_events", ["owner_id"])
        op.create_index("ix_application_events_application_id", "application_events", ["application_id"])
        op.create_index("ix_application_events_event_type", "application_events", ["event_type"])
        op.create_index("ix_application_events_occurred_at", "application_events", ["occurred_at"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("application_events", "applications"):
        if table in tables:
            op.drop_table(table)
    if "user_settings" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("user_settings")}
        if "feature_applications_enabled" in columns:
            op.drop_column("user_settings", "feature_applications_enabled")
