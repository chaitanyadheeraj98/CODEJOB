from alembic import op
import sqlalchemy as sa

revision = "20260909_0073"
down_revision = "20260909_0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("chat_turn"):
        return
    op.create_table(
        "chat_turn",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("chat_sessions.id"), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("chat_messages.id"), nullable=True),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column("requested_model", sa.String(200), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("failed_over", sa.Boolean(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("tool_calls", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("time_to_first_token_ms", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(120), nullable=True),
        sa.Column("interrupted", sa.Boolean(), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("budget_exhausted", sa.Boolean(), nullable=False),
        sa.Column("mcp_cached", sa.Boolean(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_chat_turn_session_id", "chat_turn", ["session_id"])
    op.create_index("ix_chat_turn_created_at", "chat_turn", ["created_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("chat_turn"):
        op.drop_table("chat_turn")
