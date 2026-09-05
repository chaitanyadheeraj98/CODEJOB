"""Candidate profile upload metadata.

Two columns describing the uploaded file rather than its content: which file is
loaded, and when it was uploaded. The profile text itself already lives in
`candidate_profile_markdown` from 0062.

**No index on either.** They are settings columns on a table with one row per
owner, and `temp160.md` records what a needless index on an unbounded Text
column cost when `alembic upgrade head` ran on backend boot. Nothing here is
queried by value.

`candidate_profile_uploaded_at` is nullable with no default - a NULL means "no
profile has been uploaded", which is exactly the state every existing row is in.

Revision ID: 20260904_0063
Revises: 20260904_0062
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0063"
down_revision = "20260904_0062"
branch_labels = None
depends_on = None

_TABLE = "user_settings"
_COLUMNS = ("candidate_profile_filename", "candidate_profile_uploaded_at")


def _existing() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    # Guarded so a re-run is a no-op rather than a DuplicateColumn error - the
    # boot-time `alembic upgrade head` must never be able to fail the container.
    existing = _existing()
    if "candidate_profile_filename" not in existing:
        op.add_column(
            _TABLE,
            sa.Column(
                "candidate_profile_filename",
                sa.String(length=255),
                nullable=False,
                server_default="",
            ),
        )
    if "candidate_profile_uploaded_at" not in existing:
        op.add_column(
            _TABLE,
            sa.Column("candidate_profile_uploaded_at", sa.DateTime(), nullable=True),
        )


def downgrade() -> None:
    existing = _existing()
    for column in reversed(_COLUMNS):
        if column in existing:
            op.drop_column(_TABLE, column)
