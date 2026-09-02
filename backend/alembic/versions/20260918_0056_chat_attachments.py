"""Add files attached to chat messages.

Indexes cover owner_id, session_id, message_id, and the fixed-width sha256 only.
`file_path`, `content_markdown`, and `extraction_error` are Text and are
deliberately unindexed: this migration runs on backend boot via
`alembic upgrade head`, and an index over an unbounded column blew the Postgres
btree key limit once already (see migration 0051).

Revision ID: 20260918_0056
Revises: 20260917_0055
"""

from alembic import op
import sqlalchemy as sa


revision = "20260918_0056"
down_revision = "20260917_0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("chat_attachments"):
        return

    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_attachments_id", "chat_attachments", ["id"])
    op.create_index("ix_chat_attachments_owner_id", "chat_attachments", ["owner_id"])
    op.create_index("ix_chat_attachments_session_id", "chat_attachments", ["session_id"])
    op.create_index("ix_chat_attachments_message_id", "chat_attachments", ["message_id"])
    op.create_index("ix_chat_attachments_sha256", "chat_attachments", ["sha256"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("chat_attachments"):
        op.drop_table("chat_attachments")
