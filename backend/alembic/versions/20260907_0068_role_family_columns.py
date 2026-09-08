"""Persisted role family, its confidence, and the vocabulary that produced it.

Revision ID: 20260907_0068
Revises: 20260906_0067

Adds three nullable columns; touches no existing value.

Until now the only place a JD's role family survived was inside
resume_picker_breakdown_json, an unbounded Text blob. Three consequences: a JD with
no resume selection had no family at all, nothing recorded how sure the classifier
was, and reclassifying under a new vocabulary would have meant rescoring every
email rather than running a backfill.

- role_family: the classified family. NULL on every pre-existing row, which must be
  read as "never classified" rather than backfilled with "general" - an assumed
  answer is indistinguishable from a real one, which is the defect role_source was
  added to fix (20260914_0052).
- role_family_confidence: how decisively the classifier picked it, so an aggregate
  can hold uncertain rows out instead of blending them in.
- role_family_taxonomy_version: "<system>:<version>", e.g. "builtin:1". Adopting an
  external occupation standard later becomes a value change, not another migration.

role_family is String(40) and indexed. Deliberately NOT Text: an index over the
unbounded Text column `role` exceeded Postgres's 2704-byte btree key limit on real
data and took the backend down on boot (see the withdrawn indexes in 20260913_0051).
A bounded varchar is safe to index, and role_gap_service groups on this column.

No backfill runs here. docker-compose runs `alembic upgrade head` on backend boot,
so work inside a migration is a boot-time outage risk; scripts/backfill_role_family.py
does that separately and is re-runnable.

Follows the repo's column-add convention (20260908_0046, 20260914_0052): existence
guard plus a SQLite batch_alter_table branch, so the migration is idempotent against
a create_all() schema and does not break migration tests stamped before it.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0068"
down_revision = "20260906_0067"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_recruiter_emails_role_family"
NEW_COLUMN_NAMES = ("role_family", "role_family_confidence", "role_family_taxonomy_version")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    existing = _columns("recruiter_emails")
    new_columns = [
        ("role_family", sa.Column("role_family", sa.String(length=40), nullable=True)),
        ("role_family_confidence", sa.Column("role_family_confidence", sa.Float(), nullable=True)),
        (
            "role_family_taxonomy_version",
            sa.Column("role_family_taxonomy_version", sa.String(length=40), nullable=True),
        ),
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
        op.create_index(INDEX_NAME, "recruiter_emails", ["role_family"])


def downgrade() -> None:
    if INDEX_NAME in _indexes("recruiter_emails"):
        op.drop_index(INDEX_NAME, table_name="recruiter_emails")

    existing = _columns("recruiter_emails")
    doomed = [name for name in reversed(NEW_COLUMN_NAMES) if name in existing]
    if not doomed:
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            for name in doomed:
                batch.drop_column(name)
    else:
        for name in doomed:
            op.drop_column("recruiter_emails", name)
