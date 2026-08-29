"""Link AppTS applications back to their source recruiter email.

Revision ID: 20260904_0041
Revises: 20260903_0040
"""
import re

from alembic import op
import sqlalchemy as sa

revision = "20260904_0041"
down_revision = "20260903_0040"
branch_labels = None
depends_on = None

DEDUPE_KEY_RE = re.compile(r"^appts_email:(\d+)$")


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    if "source_recruiter_email_id" not in _columns("appts_applications"):
        column = sa.Column("source_recruiter_email_id", sa.Integer(), nullable=True)
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("appts_applications") as batch:
                batch.add_column(column)
        else:
            op.add_column("appts_applications", column)
    if "ix_appts_applications_source_recruiter_email_id" not in _indexes("appts_applications"):
        op.create_index("ix_appts_applications_source_recruiter_email_id", "appts_applications", ["source_recruiter_email_id"])

    applications = sa.table("appts_applications", sa.column("id"), sa.column("dedupe_key"), sa.column("source_recruiter_email_id"))
    recruiter_emails = sa.table("recruiter_emails", sa.column("id"))
    existing_email_ids = {row[0] for row in bind.execute(sa.select(recruiter_emails.c.id))}
    for row in bind.execute(sa.select(applications.c.id, applications.c.dedupe_key)).all():
        match = DEDUPE_KEY_RE.match(row.dedupe_key or "")
        if not match:
            continue
        email_id = int(match.group(1))
        if email_id not in existing_email_ids:
            continue
        bind.execute(applications.update().where(applications.c.id == row.id).values(source_recruiter_email_id=email_id))


def downgrade() -> None:
    if "ix_appts_applications_source_recruiter_email_id" in _indexes("appts_applications"):
        op.drop_index("ix_appts_applications_source_recruiter_email_id", table_name="appts_applications")
    if "source_recruiter_email_id" in _columns("appts_applications"):
        if op.get_bind().dialect.name == "sqlite":
            with op.batch_alter_table("appts_applications") as batch:
                batch.drop_column("source_recruiter_email_id")
        else:
            op.drop_column("appts_applications", "source_recruiter_email_id")
