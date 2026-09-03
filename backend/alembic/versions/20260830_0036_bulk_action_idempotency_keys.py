"""Add bulk action idempotency keys.

Revision ID: 20260830_0036
Revises: 20260829_0035
"""
from alembic import op
import sqlalchemy as sa

revision = "20260830_0036"
down_revision = "20260829_0035"
branch_labels = None
depends_on = None

def upgrade() -> None:
    if "bulk_action_idempotency_keys" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table("bulk_action_idempotency_keys", sa.Column("owner_id", sa.String(255), primary_key=True), sa.Column("key", sa.String(64), primary_key=True), sa.Column("response_json", sa.Text(), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_bulk_action_idempotency_keys_created_at", "bulk_action_idempotency_keys", ["created_at"])

def downgrade() -> None:
    if "bulk_action_idempotency_keys" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_table("bulk_action_idempotency_keys")
