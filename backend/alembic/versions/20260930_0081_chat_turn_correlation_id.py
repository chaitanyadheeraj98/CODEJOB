"""Record which request produced a chat turn.

Revision ID: 20260930_0081
Revises: 20260929_0080

F2. §12: *"when a user says 'it hung at 3pm', that is how you find it."* The id
is minted per request, returned in the `X-Request-ID` response header and
stamped onto the turn by `ChatService._record_turn`, the only place a ChatTurn
is constructed.

Nullable, and deliberately **not** backfilled: unlike 0080's `owner_id`, which
already existed on the session, there is no id to recover for a row written
before this column. Inventing one would produce a value that appeared in no
header and no log line - an identifier that identifies nothing, which is worse
than an empty cell because it looks like an answer.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260930_0081"
down_revision = "20260929_0080"
branch_labels = None
depends_on = None

TABLE = "chat_turn"
COLUMN = "correlation_id"
INDEX = "ix_chat_turn_correlation_id"


def _has_column(bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return False
    return any(column["name"] == COLUMN for column in inspector.get_columns(TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE) or _has_column(bind):
        return
    op.add_column(TABLE, sa.Column(COLUMN, sa.String(length=64), nullable=True))
    # Indexed, like 0080's owner_id and unlike 0079's boolean: a bounded,
    # high-cardinality string whose only query is a point lookup. Without the
    # index, "find the request the user is asking about" scans a table that
    # grows with every message ever sent.
    op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    if INDEX in {index["name"] for index in sa.inspect(bind).get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
