"""Store per-owner Telegram links.

Revision ID: 20261003_0084
Revises: 20261002_0083
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261003_0084"
down_revision = "20261002_0083"
branch_labels = None
depends_on = None

TABLE = "telegram_links"
INDEX = "ix_telegram_links_owner_id"


def _columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("telegram_user_id", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("telegram_username", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("link_code_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("link_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("alerts_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("action_pin_hash", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        *_columns(),
        sa.UniqueConstraint("owner_id", name="ux_telegram_links_owner_id"),
        sa.UniqueConstraint("chat_id", name="ux_telegram_links_chat_id"),
    )
    op.create_index(INDEX, TABLE, ["owner_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_table(TABLE)
