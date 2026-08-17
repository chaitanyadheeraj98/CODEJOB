"""Add async job progress fields to recent runs.

Revision ID: 20260730_0011
Revises: 20260723_0010
Create Date: 2026-07-30 16:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260730_0011"
down_revision = "20260723_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("recent_runs")}
    additions = (
        ("job_backend_id", sa.Column("job_backend_id", sa.String(length=100), nullable=True)),
        ("total_items", sa.Column("total_items", sa.Integer(), nullable=True)),
        (
            "processed_items",
            sa.Column("processed_items", sa.Integer(), nullable=False, server_default=sa.text("0")),
        ),
        ("progress_pct", sa.Column("progress_pct", sa.Float(), nullable=True)),
        ("queue_name", sa.Column("queue_name", sa.String(length=40), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("recent_runs", column)


def downgrade() -> None:
    for name in ("queue_name", "progress_pct", "processed_items", "total_items", "job_backend_id"):
        op.drop_column("recent_runs", name)
