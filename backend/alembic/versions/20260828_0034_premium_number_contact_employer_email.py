"""Add employer_email to premium_number_contacts.

Revision ID: 20260828_0034
Revises: 20260827_0033
Create Date: 2026-08-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260828_0034"
down_revision = "20260827_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("premium_number_contacts")}
    if "employer_email" not in columns:
        op.add_column(
            "premium_number_contacts",
            sa.Column("employer_email", sa.String(length=255), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("premium_number_contacts")}
    if "employer_email" in columns:
        op.drop_column("premium_number_contacts", "employer_email")
