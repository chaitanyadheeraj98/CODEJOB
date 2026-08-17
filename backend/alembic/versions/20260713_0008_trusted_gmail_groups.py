"""Add trusted Gmail requirement groups and diagnostics.

Revision ID: 20260713_0008
Revises: 20260630_0007
Create Date: 2026-07-13 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260713_0008"
down_revision = "20260630_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "user_settings" in tables:
        settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
        if "feature_gmail_requirement_groups_enabled" not in settings_columns:
            op.add_column(
                "user_settings",
                sa.Column(
                    "feature_gmail_requirement_groups_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )

    if "gmail_requirement_groups" not in tables:
        op.create_table(
            "gmail_requirement_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("group_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("normalized_group_email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("group_slug", sa.String(length=255), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "owner_id",
                "normalized_group_email",
                name="ux_gmail_requirement_groups_owner_normalized",
            ),
        )
        op.create_index("ix_gmail_requirement_groups_owner_id", "gmail_requirement_groups", ["owner_id"])
        op.create_index("ix_gmail_requirement_groups_group_email", "gmail_requirement_groups", ["group_email"])
        op.create_index(
            "ix_gmail_requirement_groups_normalized_group_email",
            "gmail_requirement_groups",
            ["normalized_group_email"],
        )

    group_columns = (
        sa.Column("source_group_name", sa.String(length=255), nullable=True),
        sa.Column("source_group_email", sa.String(length=255), nullable=True),
        sa.Column("source_group_match_method", sa.String(length=80), nullable=True),
        sa.Column("source_group_trusted", sa.Boolean(), nullable=True),
        sa.Column("qualification_result", sa.String(length=80), nullable=True),
        sa.Column("blocking_rule", sa.String(length=120), nullable=True),
        sa.Column("qualification_detail", sa.Text(), nullable=True),
        sa.Column("qualification_context_json", sa.Text(), nullable=True),
    )
    for table_name in ("recruiter_emails", "recent_run_skipped_items"):
        if table_name not in tables:
            continue
        existing_columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        for column in group_columns:
            if column.name not in existing_columns:
                op.add_column(table_name, column.copy())


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    group_column_names = (
        "qualification_context_json",
        "qualification_detail",
        "blocking_rule",
        "qualification_result",
        "source_group_trusted",
        "source_group_match_method",
        "source_group_email",
        "source_group_name",
    )
    for table_name in ("recent_run_skipped_items", "recruiter_emails"):
        if table_name not in tables:
            continue
        existing_columns = {column["name"] for column in sa.inspect(bind).get_columns(table_name)}
        for column_name in group_column_names:
            if column_name in existing_columns:
                op.drop_column(table_name, column_name)

    if "gmail_requirement_groups" in tables:
        op.drop_table("gmail_requirement_groups")

    if "user_settings" in tables:
        settings_columns = {column["name"] for column in sa.inspect(bind).get_columns("user_settings")}
        if "feature_gmail_requirement_groups_enabled" in settings_columns:
            op.drop_column("user_settings", "feature_gmail_requirement_groups_enabled")
