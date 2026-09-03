"""Add feature_application_outreach_drafts_enabled to user_settings.

Revision ID: 20260823_0029
Revises: 20260822_0028
Create Date: 2026-08-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260823_0029"
down_revision = "20260822_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("user_settings")}
    if "feature_application_outreach_drafts_enabled" not in columns:
        op.add_column(
            "user_settings",
            sa.Column(
                "feature_application_outreach_drafts_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("user_settings")}
    if "feature_application_outreach_drafts_enabled" in columns:
        op.drop_column("user_settings", "feature_application_outreach_drafts_enabled")
