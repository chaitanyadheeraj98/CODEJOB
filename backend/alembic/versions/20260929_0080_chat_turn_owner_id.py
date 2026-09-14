"""Denormalise the owner onto chat_turn.

Revision ID: 20260929_0080
Revises: 20260928_0079

F1. A turn reached its user only through `session_id` -> `chat_sessions`, so
"this user's last 50 turns" was a join before it was a query - and that is the
shape every observability question in §12 takes.

Backfilled from `chat_sessions`, which is where the owner already lives, so no
existing row is left guessing. New rows are stamped by
`ChatService._record_turn`, the only place a ChatTurn is constructed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_0080"
down_revision = "20260928_0079"
branch_labels = None
depends_on = None

TABLE = "chat_turn"
COLUMN = "owner_id"
INDEX = "ix_chat_turn_owner_id"


def _has_column(bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return False
    return any(column["name"] == COLUMN for column in inspector.get_columns(TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE) or _has_column(bind):
        return
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.String(length=100), nullable=False, server_default="default-owner"),
    )
    # Backfill from the session rather than leaving the server default in
    # place: the default is a fallback for a row with no better answer, and
    # every existing row has one.
    op.execute(
        sa.text(
            f"UPDATE {TABLE} SET {COLUMN} = ("
            f"  SELECT s.owner_id FROM chat_sessions s WHERE s.id = {TABLE}.session_id"
            f") WHERE EXISTS ("
            f"  SELECT 1 FROM chat_sessions s WHERE s.id = {TABLE}.session_id"
            f")"
        )
    )
    # Indexed, unlike 0079's boolean: this is a bounded identifier and the
    # queries it exists for filter on it directly.
    op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    if INDEX in {index["name"] for index in sa.inspect(bind).get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
