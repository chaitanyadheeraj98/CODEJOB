"""Add resume display name setting.

Revision ID: 20260624_0004
Revises: 20260519_0003
Create Date: 2026-06-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260624_0004"
down_revision = "20260519_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "user_settings" not in table_names:
        return
    existing_cols = {row["name"] for row in inspector.get_columns("user_settings")}
    if "resume_display_name" not in existing_cols:
        op.add_column(
            "user_settings",
            sa.Column("resume_display_name", sa.String(length=255), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())
    if "user_settings" not in table_names:
        return
    existing_cols = {row["name"] for row in inspector.get_columns("user_settings")}
    if "resume_display_name" in existing_cols:
        op.drop_column("user_settings", "resume_display_name")
