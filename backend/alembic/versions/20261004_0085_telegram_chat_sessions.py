"""Bind Telegram chats to persisted assistant sessions.

Revision ID: 20261004_0085
Revises: 20261003_0084
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261004_0085"
down_revision = "20261003_0084"
branch_labels = None
depends_on = None

SESSION_TABLE = "chat_sessions"
LINK_TABLE = "telegram_links"
INDEX = "ix_chat_sessions_origin_updated_at"
FOREIGN_KEY = "fk_telegram_links_chat_session_id_chat_sessions"


def _columns() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="web"),
        sa.Column("chat_session_id", sa.Integer(), nullable=True),
    )


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    origin, chat_session_id = _columns()
    if not _has_column(bind, SESSION_TABLE, "origin"):
        op.add_column(SESSION_TABLE, origin)
    if INDEX not in {item["name"] for item in sa.inspect(bind).get_indexes(SESSION_TABLE)}:
        op.create_index(INDEX, SESSION_TABLE, ["origin", "updated_at"])
    if not _has_column(bind, LINK_TABLE, "chat_session_id"):
        with op.batch_alter_table(LINK_TABLE) as batch:
            batch.add_column(chat_session_id)
            batch.create_foreign_key(FOREIGN_KEY, SESSION_TABLE, ["chat_session_id"], ["id"])


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, LINK_TABLE, "chat_session_id"):
        with op.batch_alter_table(LINK_TABLE) as batch:
            batch.drop_constraint(FOREIGN_KEY, type_="foreignkey")
            batch.drop_column("chat_session_id")
    if INDEX in {item["name"] for item in sa.inspect(bind).get_indexes(SESSION_TABLE)}:
        op.drop_index(INDEX, table_name=SESSION_TABLE)
    if _has_column(bind, SESSION_TABLE, "origin"):
        op.drop_column(SESSION_TABLE, "origin")
