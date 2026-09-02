"""Provenance for company, mirroring role_source.

Revision ID: 20260915_0053
Revises: 20260914_0052

Adds one nullable column; touches no existing value. NULL keeps meaning
"unverified" - it is never backfilled with an assumption about history.

This exists so a company filled from the approved taxonomy is distinguishable
from one the parser extracted. Writing a matched value with no
marker would reintroduce exactly the silent-plausible-wrong-answer problem that
role_source was added to remove.

No index: this column is not filtered on, and an unnecessary index over a column
is how migration 0051 took the backend down. Follows the repo's column-add
convention (20260908_0046): existence guard plus a SQLite batch_alter_table
branch, so it is idempotent against a create_all() schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260915_0053"
down_revision = "20260914_0052"
branch_labels = None
depends_on = None

NEW_COLUMNS = ("company_source",)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("recruiter_emails")
    pending = [name for name in NEW_COLUMNS if name not in existing]
    if not pending:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            for name in pending:
                batch.add_column(sa.Column(name, sa.String(length=40), nullable=True))
    else:
        for name in pending:
            op.add_column("recruiter_emails", sa.Column(name, sa.String(length=40), nullable=True))


def downgrade() -> None:
    existing = _columns("recruiter_emails")
    doomed = [name for name in reversed(NEW_COLUMNS) if name in existing]
    if not doomed:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            for name in doomed:
                batch.drop_column(name)
    else:
        for name in doomed:
            op.drop_column("recruiter_emails", name)
