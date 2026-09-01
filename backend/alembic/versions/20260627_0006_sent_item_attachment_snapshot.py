"""Persist sent attachment history for recruiter emails.

Revision ID: 20260627_0006
Revises: 20260624_0005
Create Date: 2026-06-27 13:45:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260627_0006"
down_revision = "20260624_0005"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    if "sent_attachment_file_names_json" in _columns(bind, "recruiter_emails"):
        return
    op.add_column("recruiter_emails", sa.Column("sent_attachment_file_names_json", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    if "sent_attachment_file_names_json" not in _columns(bind, "recruiter_emails"):
        return
    op.drop_column("recruiter_emails", "sent_attachment_file_names_json")
