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


def upgrade() -> None:
    op.add_column("recruiter_emails", sa.Column("sent_attachment_file_names_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recruiter_emails", "sent_attachment_file_names_json")
