"""Encrypted per-owner provider credentials.

Revision ID: 20260927_0078
Revises: 20260926_0077
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_0078"
down_revision = "20260926_0077"
branch_labels = None
depends_on = None

TABLE = "provider_credentials"


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False, server_default=""),
        sa.Column("base_url", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("owner_id", "provider", name="ux_provider_credentials_owner_provider"),
    )
    op.create_index("ix_provider_credentials_owner_id", TABLE, ["owner_id"])
    op.create_index("ix_provider_credentials_provider", TABLE, ["provider"])


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.drop_index("ix_provider_credentials_provider", table_name=TABLE)
    op.drop_index("ix_provider_credentials_owner_id", table_name=TABLE)
    op.drop_table(TABLE)
