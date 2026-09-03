"""Add application suggestions and matching preferences.

Revision ID: 20260822_0028
Revises: 20260821_0027
Create Date: 2026-08-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260822_0028"
down_revision = "20260821_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if "user_settings" in tables:
        columns = {column["name"] for column in inspector.get_columns("user_settings")}
        additions = (
            ("preferred_employment_types_json", sa.Text(), False, "[]"),
            ("preferred_minimum_rate", sa.Float(), True, None),
            ("feature_application_automation_enabled", sa.Boolean(), False, sa.false()),
            ("feature_reminder_sweep_interval_minutes", sa.Integer(), False, "240"),
        )
        for name, column_type, nullable, default in additions:
            if name not in columns:
                op.add_column(
                    "user_settings",
                    sa.Column(name, column_type, nullable=nullable, server_default=default),
                )

    if "application_suggestions" not in tables:
        op.create_table(
            "application_suggestions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("application_id", sa.Integer(), nullable=False),
            sa.Column("suggestion_type", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("confidence", sa.String(length=10), nullable=False, server_default="high"),
            sa.Column("reply_message_id", sa.Integer(), nullable=True),
            sa.Column("recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("suggested_status", sa.String(length=40), nullable=True),
            sa.Column("suggested_next_action_type", sa.String(length=80), nullable=True),
            sa.Column("suggested_next_action_at", sa.DateTime(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_application_suggestions_owner_id", "application_suggestions", ["owner_id"])
        op.create_index("ix_application_suggestions_application_id", "application_suggestions", ["application_id"])
        op.create_index("ix_application_suggestions_suggestion_type", "application_suggestions", ["suggestion_type"])
        op.create_index("ix_application_suggestions_status", "application_suggestions", ["status"])
        op.create_index("ix_application_suggestions_created_at", "application_suggestions", ["created_at"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "application_suggestions" in tables:
        op.drop_table("application_suggestions")
    if "user_settings" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("user_settings")}
        for name in (
            "feature_reminder_sweep_interval_minutes",
            "feature_application_automation_enabled",
            "preferred_minimum_rate",
            "preferred_employment_types_json",
        ):
            if name in columns:
                op.drop_column("user_settings", name)
