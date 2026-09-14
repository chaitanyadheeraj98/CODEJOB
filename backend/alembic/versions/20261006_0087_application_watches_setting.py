"""Add the per-owner application watches setting.

Revision ID: 20261006_0087
Revises: 20261005_0086
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261006_0087"
down_revision = "20261005_0086"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "user_settings", "feature_application_watches_enabled"):
        op.add_column(
            "user_settings",
            sa.Column("feature_application_watches_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "user_settings", "feature_application_watches_enabled"):
        op.drop_column("user_settings", "feature_application_watches_enabled")
