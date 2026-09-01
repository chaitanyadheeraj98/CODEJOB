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


NEW_COLUMNS = ("content_markdown", "content_summary", "content_evidence_json")


def upgrade() -> None:
    bind = op.get_bind()
    if "resume_assets" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("resume_assets")}
    for name in NEW_COLUMNS:
        if name not in columns:
            op.add_column("resume_assets", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if "resume_assets" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("resume_assets")}
    for name in reversed(NEW_COLUMNS):
        if name in columns:
            op.drop_column("resume_assets", name)
