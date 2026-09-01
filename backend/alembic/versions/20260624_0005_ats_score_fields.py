"""add ats score fields

Revision ID: 20260624_0005
Revises: 20260624_0004
Create Date: 2026-06-24 13:25:00
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0005"
down_revision = "20260624_0004"
branch_labels = None
depends_on = None


NEW_COLUMNS = (
    ("ats_score", lambda: sa.Column("ats_score", sa.Float(), nullable=True)),
    ("ats_score_source", lambda: sa.Column("ats_score_source", sa.String(length=80), nullable=True)),
    ("ats_summary", lambda: sa.Column("ats_summary", sa.Text(), nullable=True)),
    ("ats_breakdown_json", lambda: sa.Column("ats_breakdown_json", sa.Text(), nullable=True)),
)


def upgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")}
    for name, column in NEW_COLUMNS:
        if name not in columns:
            op.add_column("recruiter_emails", column())


def downgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")}
    for name, _ in reversed(NEW_COLUMNS):
        if name in columns:
            op.drop_column("recruiter_emails", name)
