"""Add one-time extracted resume content and evidence fields.

Revision ID: 20260820_0024
Revises: 20260820_0023
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260820_0024"
down_revision = "20260820_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("resume_assets", sa.Column("content_markdown", sa.Text(), nullable=True))
    op.add_column("resume_assets", sa.Column("content_summary", sa.Text(), nullable=True))
    op.add_column("resume_assets", sa.Column("content_evidence_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("resume_assets", "content_evidence_json")
    op.drop_column("resume_assets", "content_summary")
    op.drop_column("resume_assets", "content_markdown")
