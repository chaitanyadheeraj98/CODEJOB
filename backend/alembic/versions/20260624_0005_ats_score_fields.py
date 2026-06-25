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


def upgrade() -> None:
    op.add_column("recruiter_emails", sa.Column("ats_score", sa.Float(), nullable=True))
    op.add_column("recruiter_emails", sa.Column("ats_score_source", sa.String(length=80), nullable=True))
    op.add_column("recruiter_emails", sa.Column("ats_summary", sa.Text(), nullable=True))
    op.add_column("recruiter_emails", sa.Column("ats_breakdown_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("recruiter_emails", "ats_breakdown_json")
    op.drop_column("recruiter_emails", "ats_summary")
    op.drop_column("recruiter_emails", "ats_score_source")
    op.drop_column("recruiter_emails", "ats_score")
