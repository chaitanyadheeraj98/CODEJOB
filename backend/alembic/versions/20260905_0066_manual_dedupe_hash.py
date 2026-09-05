"""Add the manual-intake content fingerprint to recruiter_emails.

Manual intake accepts a pasted requirement, which arrives with no delivery
identity of its own: Gmail dedupes on `external_message_id` and Nvoids on the
listing URL, and a paste has neither. So the same text pasted twice is
recognised by hashing its normalized content - the same
`build_dedupe_hash` the Nvoids sync already uses - and storing the result here.

Set on manual rows only; NULL for gmail and nvoids. Nullable, so the add is
metadata-only on Postgres and the existing rows are untouched.

The index is over a fixed-width String(40). It has to stay that way: this
migration runs on backend boot via `alembic upgrade head`, and an index over an
unbounded column blew the Postgres btree key limit once already (migration 0051).

Revision ID: 20260905_0066
Revises: 20260904_0065
"""

from alembic import op
import sqlalchemy as sa


revision = "20260905_0066"
down_revision = "20260904_0065"
branch_labels = None
depends_on = None

_TABLE = "recruiter_emails"
_COLUMN = "manual_dedupe_hash"
_INDEX = "ix_recruiter_emails_manual_dedupe_hash"


def _has_column(inspector: sa.Inspector) -> bool:
    return any(row["name"] == _COLUMN for row in inspector.get_columns(_TABLE))


def _has_index(inspector: sa.Inspector) -> bool:
    return any(row["name"] == _INDEX for row in inspector.get_indexes(_TABLE))


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return
    if not _has_column(inspector):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=40), nullable=True))
    # Re-inspected rather than assumed: a database that already had the column
    # from create_all() may still be missing the index.
    if not _has_index(sa.inspect(op.get_bind())):
        op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_TABLE):
        return
    if _has_index(inspector):
        op.drop_index(_INDEX, table_name=_TABLE)
    if _has_column(sa.inspect(op.get_bind())):
        op.drop_column(_TABLE, _COLUMN)
