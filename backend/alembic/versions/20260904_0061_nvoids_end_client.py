"""Nvoids end-client search criteria.

Two settings columns for `temp162.md` §16.6: the company to search nvoids for,
and whether that company composes with the existing role and location criteria or
replaces them.

**No index on either.** They are settings on a table with one row per owner, and
`temp160.md` records what a needless index on an unbounded Text column cost when
`alembic upgrade head` ran on backend boot. Nothing here is queried by value.

Both columns are nullable with a server default, so the upgrade is additive and a
running backend on the previous revision keeps working.

Revision ID: 20260904_0061
Revises: 20260922_0060
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0061"
down_revision = "20260922_0060"
branch_labels = None
depends_on = None

_TABLE = "user_settings"
_COLUMNS = ("nvoids_end_client", "nvoids_query_mode")


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded so a re-run is a no-op rather than a DuplicateColumn error - the
    # boot-time `alembic upgrade head` must never be able to fail the container.
    existing = _existing()
    if "nvoids_end_client" not in existing:
        op.add_column(
            _TABLE,
            sa.Column("nvoids_end_client", sa.Text(), nullable=False, server_default=""),
        )
    if "nvoids_query_mode" not in existing:
        op.add_column(
            _TABLE,
            sa.Column(
                "nvoids_query_mode",
                sa.String(length=40),
                nullable=False,
                server_default="composed",
            ),
        )


def downgrade() -> None:
    existing = _existing()
    for column in reversed(_COLUMNS):
        if column in existing:
            op.drop_column(_TABLE, column)
