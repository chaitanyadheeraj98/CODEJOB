"""Add configurable Nvoids search fields: job role, search location, custom query.

Revision ID: 20260820_0023
Revises: 20260818_0022
Create Date: 2026-08-20
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260820_0023"
down_revision = "20260818_0022"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _add_column(column: sa.Column) -> None:
    if column.name not in _columns("user_settings"):
        op.add_column("user_settings", column)


def upgrade() -> None:
    _add_column(sa.Column("nvoids_job_role", sa.Text(), nullable=False, server_default=""))
    _add_column(sa.Column("nvoids_search_location", sa.Text(), nullable=False, server_default=""))
    _add_column(sa.Column("nvoids_custom_query", sa.Text(), nullable=False, server_default=""))


def downgrade() -> None:
    for column_name in ("nvoids_custom_query", "nvoids_search_location", "nvoids_job_role"):
        if column_name in _columns("user_settings"):
            op.drop_column("user_settings", column_name)
