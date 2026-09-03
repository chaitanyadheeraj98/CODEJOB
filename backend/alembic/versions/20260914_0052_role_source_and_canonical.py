"""Provenance and canonical title for RecruiterEmail.role.

Revision ID: 20260914_0052
Revises: 20260913_0051

Adds two nullable columns; touches no existing value.

- role_source: where the stored role came from (extracted / taxonomy_matched /
  subject_fallback / source_parent / unknown). NULL on every pre-existing row,
  which must be read as "unverified" rather than backfilled with an assumption.
- role_canonical: collapsed taxonomy title used for aggregation, kept separate
  so `role` can stay specific for the draft copy that interpolates it.

role_canonical is String(255) and indexed. It is deliberately NOT Text: an index
over the unbounded Text column `role` exceeded Postgres's 2704-byte btree key
limit on real data and took the backend down on boot (see the withdrawn indexes
in 20260913_0051). A bounded varchar is safe to index.

Follows the repo's column-add convention (20260908_0046): existence guard plus a
SQLite batch_alter_table branch, so the migration is idempotent against a
create_all() schema and does not break every migration test stamped before it.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260914_0052"
down_revision = "20260913_0051"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_recruiter_emails_role_canonical"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    existing = _columns("recruiter_emails")
    new_columns = [
        ("role_source", sa.Column("role_source", sa.String(length=40), nullable=True)),
        ("role_canonical", sa.Column("role_canonical", sa.String(length=255), nullable=True)),
    ]
    pending = [(name, column) for name, column in new_columns if name not in existing]
    if pending:
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("recruiter_emails") as batch:
                for _name, column in pending:
                    batch.add_column(column)
        else:
            for _name, column in pending:
                op.add_column("recruiter_emails", column)

    if INDEX_NAME not in _indexes("recruiter_emails"):
        op.create_index(INDEX_NAME, "recruiter_emails", ["role_canonical"])


def downgrade() -> None:
    if INDEX_NAME in _indexes("recruiter_emails"):
        op.drop_index(INDEX_NAME, table_name="recruiter_emails")

    existing = _columns("recruiter_emails")
    doomed = [name for name in ("role_canonical", "role_source") if name in existing]
    if not doomed:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            for name in doomed:
                batch.drop_column(name)
    else:
        for name in doomed:
            op.drop_column("recruiter_emails", name)
