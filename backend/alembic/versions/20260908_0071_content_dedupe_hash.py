"""Add the gmail content fingerprint to recruiter_emails.

Gmail currently dedupes only on delivery identity (`external_message_id`) -
but a recruiter's system can resend the exact same email under a new message
id, and that isn't hypothetical: it happened in prod and produced two
needs_review rows for one requirement. This column fingerprints (sender,
subject, body) via `build_email_content_hash`, so a resend is recognised
even though its delivery identity differs.

Set on gmail rows only. NULL for manual intake, which already has its own
fingerprint (`manual_dedupe_hash`) tuned to a different contract (day-bucketed,
so a re-paste next week isn't flagged - a resend of the same email should be).
NULL for nvoids too: it dedupes on the listing URL, a stable identifier that
doesn't change on a resend the way a Gmail message id does, so it isn't
exposed to this failure mode.

The index is over a fixed-width String(40), same as manual_dedupe_hash. It has
to stay that way: this migration runs on backend boot via `alembic upgrade
head`, and an index over an unbounded column blew the Postgres btree key limit
once already (migration 0051).

Revision ID: 20260908_0071
Revises: 20260909_0070
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0071"
down_revision = "20260909_0070"
branch_labels = None
depends_on = None

_TABLE = "recruiter_emails"
_COLUMN = "content_dedupe_hash"
_INDEX = "ix_recruiter_emails_content_dedupe_hash"


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
