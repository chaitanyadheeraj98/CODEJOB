"""Add notified_at to email_reply_messages.

Revision ID: 20260824_0030
Revises: 20260823_0029
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260824_0030"
down_revision = "20260823_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("email_reply_messages")}
    if "notified_at" not in columns:
        op.add_column(
            "email_reply_messages",
            sa.Column("notified_at", sa.DateTime(), nullable=True),
        )
        # Backfill existing rows as already-notified so the proactive-notification
        # feature only reacts to replies that arrive after this migration, not a
        # retroactive burst over every reply already in the inbox.
        op.execute(
            "UPDATE email_reply_messages SET notified_at = received_at WHERE notified_at IS NULL"
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("email_reply_messages")}
    if "notified_at" in columns:
        op.drop_column("email_reply_messages", "notified_at")
