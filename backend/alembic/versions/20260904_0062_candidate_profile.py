"""Candidate profile Markdown.

One settings column holding the user's own account of themselves. The assistant
reads it when it has to write *as* the user rather than about a candidate, so it
is free text and is never parsed by the screening rules.

**No index.** It is a settings column on a table with one row per owner, and
`temp160.md` records what a needless index on an unbounded Text column cost when
`alembic upgrade head` ran on backend boot. Nothing here is queried by value.

Nullable with a server default, so the upgrade is additive and a running backend
on the previous revision keeps working.

Revision ID: 20260904_0062
Revises: 20260904_0061
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0062"
down_revision = "20260904_0061"
branch_labels = None
depends_on = None

_TABLE = "user_settings"
_COLUMN = "candidate_profile_markdown"


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded so a re-run is a no-op rather than a DuplicateColumn error - the
    # boot-time `alembic upgrade head` must never be able to fail the container.
    if _COLUMN not in _existing():
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    if _COLUMN in _existing():
        op.drop_column(_TABLE, _COLUMN)
