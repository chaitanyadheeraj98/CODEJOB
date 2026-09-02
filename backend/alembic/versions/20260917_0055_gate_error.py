"""Persist the intent-gate error alongside the provider.

`EmailIntentDecision.error` was computed on every failure and thrown away: the write
sites set `gate_provider` and nothing else. Because `gate_provider` distinguishes
`groq` from `groq_fallback_taxonomy`, the historical record shows that roughly half
of all gate calls silently degraded to the rules taxonomy - but not one row says
whether that was a timeout, a rate limit, or a response that failed schema
validation. Those three want different fixes, and the difference decides whether
moving to a provider with weaker output guarantees is safe at all.

Nullable String(80): these are short slugs. Deliberately **no index** - it is a
diagnostic column read by grouping queries, not a filter, and an index over a column
like this buys nothing while adding DDL risk on boot.

Revision ID: 20260917_0055
Revises: 20260916_0054
"""
from alembic import op
import sqlalchemy as sa

revision = "20260917_0055"
down_revision = "20260916_0054"
branch_labels = None
depends_on = None

TABLES = ("recruiter_emails", "recent_run_skipped_items")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in TABLES:
        if "gate_error" in _columns(table):
            continue
        column = sa.Column("gate_error", sa.String(80), nullable=True)
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch:
                batch.add_column(column)
        else:
            op.add_column(table, column)


def downgrade() -> None:
    for table in TABLES:
        if "gate_error" not in _columns(table):
            continue
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch:
                batch.drop_column("gate_error")
        else:
            op.drop_column(table, "gate_error")
