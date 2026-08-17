"""Add persisted in-app chat sessions and messages.

Revision ID: 20260814_0015
Revises: 20260810_0014
Create Date: 2026-08-14 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260814_0015"
down_revision = "20260810_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=120), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_sessions_id", "chat_sessions", ["id"])
        op.create_index("ix_chat_sessions_owner_id", "chat_sessions", ["owner_id"])

    inspector = sa.inspect(bind)
    if not inspector.has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=20), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("tool_name", sa.String(length=120), nullable=True),
            sa.Column("tool_call_args", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_chat_messages_id", "chat_messages", ["id"])
        op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("chat_messages"):
        op.drop_table("chat_messages")
    if inspector.has_table("chat_sessions"):
        op.drop_table("chat_sessions")
