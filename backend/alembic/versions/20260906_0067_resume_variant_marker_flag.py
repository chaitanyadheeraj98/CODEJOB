"""Feature flag for the invisible resume-variant marker on outgoing mail.

Index safety, restated because this migration runs on backend boot via
`alembic upgrade head` and migration 0051 took production down with an index on
an unbounded Text column:

This migration adds one Boolean column and creates no index at all. Nothing here
can repeat that failure.

The column defaults to true so existing installs keep the behaviour they already
have - the marker shipped before the switch existed, and a migration should not
silently turn a live feature off. `server_default` is set for the backfill of
existing rows and then dropped, so the application default in the model stays the
single source of truth for new rows.

Revision ID: 20260906_0067
Revises: 20260905_0066
"""

from alembic import op
import sqlalchemy as sa


revision = "20260906_0067"
down_revision = "20260905_0066"
branch_labels = None
depends_on = None

_TABLE = "user_settings"
_COLUMN = "feature_resume_variant_marker_enabled"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Existence guard, per the shape in 20260908_0046: without it every migration
    # test stamped before this revision dies on "duplicate column name".
    if _COLUMN in _columns(_TABLE):
        return
    column = sa.Column(_COLUMN, sa.Boolean(), nullable=False, server_default=sa.true())
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(column)
    else:
        op.add_column(_TABLE, column)
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(_COLUMN, server_default=None)


def downgrade() -> None:
    if _COLUMN not in _columns(_TABLE):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
    else:
        op.drop_column(_TABLE, _COLUMN)
