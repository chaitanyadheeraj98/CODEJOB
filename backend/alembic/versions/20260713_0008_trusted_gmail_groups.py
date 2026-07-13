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
    op.add_column(
        "user_settings",
        sa.Column("feature_gmail_requirement_groups_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "gmail_requirement_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("group_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("normalized_group_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("group_slug", sa.String(length=255), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("owner_id", "normalized_group_email", name="ux_gmail_requirement_groups_owner_normalized"),
    )
    op.create_index("ix_gmail_requirement_groups_owner_id", "gmail_requirement_groups", ["owner_id"])
    op.create_index("ix_gmail_requirement_groups_group_email", "gmail_requirement_groups", ["group_email"])
    op.create_index("ix_gmail_requirement_groups_normalized_group_email", "gmail_requirement_groups", ["normalized_group_email"])

    for table_name in ("recruiter_emails", "recent_run_skipped_items"):
        op.add_column(table_name, sa.Column("source_group_name", sa.String(length=255), nullable=True))
        op.add_column(table_name, sa.Column("source_group_email", sa.String(length=255), nullable=True))
        op.add_column(table_name, sa.Column("source_group_match_method", sa.String(length=80), nullable=True))
        op.add_column(table_name, sa.Column("source_group_trusted", sa.Boolean(), nullable=True))
        op.add_column(table_name, sa.Column("qualification_result", sa.String(length=80), nullable=True))
        op.add_column(table_name, sa.Column("blocking_rule", sa.String(length=120), nullable=True))
        op.add_column(table_name, sa.Column("qualification_detail", sa.Text(), nullable=True))
        op.add_column(table_name, sa.Column("qualification_context_json", sa.Text(), nullable=True))


def downgrade() -> None:
    for table_name in ("recent_run_skipped_items", "recruiter_emails"):
        op.drop_column(table_name, "qualification_context_json")
        op.drop_column(table_name, "qualification_detail")
        op.drop_column(table_name, "blocking_rule")
        op.drop_column(table_name, "qualification_result")
        op.drop_column(table_name, "source_group_trusted")
        op.drop_column(table_name, "source_group_match_method")
        op.drop_column(table_name, "source_group_email")
        op.drop_column(table_name, "source_group_name")

    op.drop_index("ix_gmail_requirement_groups_normalized_group_email", table_name="gmail_requirement_groups")
    op.drop_index("ix_gmail_requirement_groups_group_email", table_name="gmail_requirement_groups")
    op.drop_index("ix_gmail_requirement_groups_owner_id", table_name="gmail_requirement_groups")
    op.drop_table("gmail_requirement_groups")

    op.drop_column("user_settings", "feature_gmail_requirement_groups_enabled")
