"""Add recent run headers and skipped-item audit rows.

Revision ID: 20260630_0007
Revises: 20260627_0006
Create Date: 2026-06-30 19:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260630_0007"
down_revision = "20260627_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recent_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("run_source", sa.String(length=40), nullable=False),
        sa.Column("run_key", sa.String(length=160), nullable=False),
        sa.Column("sync_batch_id", sa.String(length=100), nullable=True),
        sa.Column("external_scrape_run_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="ok"),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("matched_count", sa.Integer(), nullable=True),
        sa.Column("queued_count", sa.Integer(), nullable=True),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_recent_runs_owner_id", "recent_runs", ["owner_id"])
    op.create_index("ix_recent_runs_run_source", "recent_runs", ["run_source"])
    op.create_index("ix_recent_runs_run_key", "recent_runs", ["run_key"], unique=True)

    op.create_table(
        "recent_run_skipped_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("run_source", sa.String(length=40), nullable=False),
        sa.Column("run_key", sa.String(length=160), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False, server_default="gmail"),
        sa.Column("outcome", sa.String(length=40), nullable=False, server_default="skipped"),
        sa.Column("reason_code", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("reason_detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_message_id", sa.String(length=255), nullable=True),
        sa.Column("external_thread_id", sa.String(length=1200), nullable=True),
        sa.Column("candidate_email_id", sa.Integer(), nullable=True),
        sa.Column("external_opportunity_id", sa.Integer(), nullable=True),
        sa.Column("title_or_subject", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("sender", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=1200), nullable=True),
        sa.Column("gmail_message_url", sa.String(length=1200), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_recent_run_skipped_items_owner_id", "recent_run_skipped_items", ["owner_id"])
    op.create_index("ix_recent_run_skipped_items_run_key", "recent_run_skipped_items", ["run_key"])
    op.create_index("ix_recent_run_skipped_items_reason_code", "recent_run_skipped_items", ["reason_code"])


def downgrade() -> None:
    op.drop_index("ix_recent_run_skipped_items_reason_code", table_name="recent_run_skipped_items")
    op.drop_index("ix_recent_run_skipped_items_run_key", table_name="recent_run_skipped_items")
    op.drop_index("ix_recent_run_skipped_items_owner_id", table_name="recent_run_skipped_items")
    op.drop_table("recent_run_skipped_items")

    op.drop_index("ix_recent_runs_run_key", table_name="recent_runs")
    op.drop_index("ix_recent_runs_run_source", table_name="recent_runs")
    op.drop_index("ix_recent_runs_owner_id", table_name="recent_runs")
    op.drop_table("recent_runs")
