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


def upgrade() -> None:
    op.add_column(
        "premium_number_leads",
        sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("premium_number_leads", "linkedin_url")
