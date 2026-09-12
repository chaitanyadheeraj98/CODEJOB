"""Record self-service account deletion requests.

Revision ID: 20261001_0082
Revises: 20260930_0081
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_0082"
down_revision = "20260930_0081"
branch_labels = None
depends_on = None

TABLE = "users"
COLUMN = "deletion_requested_at"
INDEX = "ix_users_deletion_requested_at"


def _has_column(bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(TABLE) and any(
        column["name"] == COLUMN for column in inspector.get_columns(TABLE)
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(TABLE) or _has_column(bind):
        return
    op.add_column(TABLE, sa.Column(COLUMN, sa.DateTime(timezone=True), nullable=True))
    op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind):
        return
    if INDEX in {index["name"] for index in sa.inspect(bind).get_indexes(TABLE)}:
        op.drop_index(INDEX, table_name=TABLE)
    op.drop_column(TABLE, COLUMN)
