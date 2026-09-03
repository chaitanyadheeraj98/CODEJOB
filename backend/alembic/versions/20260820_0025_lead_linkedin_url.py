"""Add linkedin_url to premium_number_leads.

Revision ID: 20260820_0025
Revises: 20260820_0024
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260820_0025"
down_revision = "20260820_0024"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if "premium_number_leads" not in sa.inspect(bind).get_table_names():
        return
    if "linkedin_url" in _columns(bind, "premium_number_leads"):
        return
    op.add_column(
        "premium_number_leads",
        sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "premium_number_leads" not in sa.inspect(bind).get_table_names():
        return
    if "linkedin_url" not in _columns(bind, "premium_number_leads"):
        return
    op.drop_column("premium_number_leads", "linkedin_url")
