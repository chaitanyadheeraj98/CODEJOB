"""Gmail push (Pub/Sub) watch state on the existing credential row.

Six columns, no new table and no new index. `owner_id` is already unique and
`google_email` is already indexed - 0076 indexed it specifically so a Pub/Sub
notification, which identifies a mailbox by address and nothing else, could
find its row. There is nothing here to filter on that is not already covered,
and an index on a column only ever read through one of those is cost without a
query to pay for it.

Existing rows land with a null cursor and null timestamps, which is exactly
what "this mailbox has never had a watch" means. The first registration fills
them in; nothing needs backfilling.

Replay-safe: adds only the columns that are absent, and the downgrade removes
only those six. Following 0076's own note - 0074 was not replay-safe and
`test_migration_0014` failed on a second run until it was guarded.

Revision ID: 20261002_0083
Revises: 20261001_0082
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261002_0083"
down_revision = "20261001_0082"
branch_labels = None
depends_on = None

TABLE = "gmail_credentials"

def _columns() -> tuple[sa.Column, ...]:
    """Fresh `Column` objects on every call, not a module-level tuple.

    `op.add_column` attaches the object it is handed to a table, and a second
    attachment raises `ArgumentError: Column object 'x' already assigned`. A
    module constant is therefore single-use.

    Nothing in this repository's test suite fails without this, and that is
    worth saying rather than implying otherwise: alembic re-executes a revision
    file on every `command.upgrade`, so the constant is rebuilt each time and
    the replay test never sees a second attachment. It bites whoever calls
    `upgrade()` twice against one loaded module. 0008 solves the same problem
    with `column.copy()`; a factory is the version that does not need a
    deprecated method.
    """
    return (
        sa.Column("gmail_history_id", sa.String(length=64), nullable=True),
        sa.Column("gmail_watch_expiration_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_watch_renewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_last_notification_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmail_last_event_processed_at", sa.DateTime(timezone=True), nullable=True),
        # Non-null to match the model, so a caller reading it never has to
        # decide what a null error means. The server default is what lets an
        # existing row take the column without a rewrite.
        sa.Column("gmail_watch_last_error", sa.Text(), nullable=False, server_default=""),
    )


COLUMN_NAMES = tuple(column.name for column in _columns())


def _existing(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return set()
    return {column["name"] for column in inspector.get_columns(TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE):
        return
    present = _existing(bind)
    for column in _columns():
        if column.name not in present:
            op.add_column(TABLE, column)


def downgrade() -> None:
    bind = op.get_bind()
    present = _existing(bind)
    # Reverse order so a partially applied upgrade unwinds the way it was
    # built. Nothing here depends on ordering today; it costs nothing to keep
    # that true if one of these ever gains a constraint.
    for name in reversed(COLUMN_NAMES):
        if name in present:
            op.drop_column(TABLE, name)
