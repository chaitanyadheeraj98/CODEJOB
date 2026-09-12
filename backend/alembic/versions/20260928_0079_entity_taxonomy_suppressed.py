"""Let an owner tombstone a base taxonomy entry.

Revision ID: 20260928_0079
Revises: 20260927_0078

E2 resolves entity taxonomy as `overlay ?? base`. Without a tombstone the base
can only ever be added to: a user who disagrees with a bundled entry has no way
to remove it, because their own row for the same key would be an *addition*
rather than a correction.

`suppressed` is that tombstone. It is a column on the owner's row rather than a
deletion, so the artefact stays the artefact - the user's decision is recorded
against it and survives the next base regeneration.

Defaulted false with a server default, so the backfill is the default: every
existing row keeps exactly the behaviour it had.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_0079"
down_revision = "20260927_0078"
branch_labels = None
depends_on = None

TABLE = "canonical_entity_taxonomy_entries"
COLUMN = "suppressed"


def _has_column(bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(TABLE):
        return False
    return any(column["name"] == COLUMN for column in inspector.get_columns(TABLE))


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE) or _has_column(bind):
        return
    # server_default rather than a data migration: the default *is* the
    # backfill, and it keeps the column non-null for rows written by a process
    # still running the previous image during a rolling deploy.
    op.add_column(
        TABLE,
        sa.Column(COLUMN, sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Deliberately not indexed. It is read as part of an owner+entity_type scan
    # that is already indexed, and a boolean with one dominant value indexes
    # badly - see the incident behind the index discipline in the plan.


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    op.drop_column(TABLE, COLUMN)
