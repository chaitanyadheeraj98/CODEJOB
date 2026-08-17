"""Add email open tracking and reply inbox tables.

Revision ID: 20260805_0013
Revises: 20260801_0012
Create Date: 2026-08-05 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260805_0013"
down_revision = "20260801_0012"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "recruiter_emails" in tables:
        recruiter_columns = _columns(inspector, "recruiter_emails")
        for name, column in (
            ("tracking_token", sa.Column("tracking_token", sa.String(length=64), nullable=True)),
            ("opened_at", sa.Column("opened_at", sa.DateTime(), nullable=True)),
            ("open_count", sa.Column("open_count", sa.Integer(), nullable=False, server_default="0")),
        ):
            if name not in recruiter_columns:
                op.add_column("recruiter_emails", column)
        recruiter_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("recruiter_emails")
        }
        if "ix_recruiter_emails_tracking_token" not in recruiter_indexes:
            op.create_index(
                "ix_recruiter_emails_tracking_token",
                "recruiter_emails",
                ["tracking_token"],
                unique=True,
            )

    if "user_settings" in tables:
        settings_columns = _columns(sa.inspect(bind), "user_settings")
        for name in ("feature_email_tracking_enabled", "feature_reply_inbox_enabled"):
            if name not in settings_columns:
                op.add_column(
                    "user_settings",
                    sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()),
                )

    if "email_open_events" not in tables:
        op.create_table(
            "email_open_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("recruiter_email_id", sa.Integer(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("user_agent", sa.Text(), nullable=False, server_default=""),
            sa.Column("remote_ip", sa.String(length=100), nullable=False, server_default=""),
            sa.Column("is_likely_proxy", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
        op.create_index("ix_email_open_events_owner_id", "email_open_events", ["owner_id"])
        op.create_index("ix_email_open_events_recruiter_email_id", "email_open_events", ["recruiter_email_id"])

    if "email_conversations" not in tables:
        op.create_table(
            "email_conversations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("root_recruiter_email_id", sa.Integer(), nullable=False),
            sa.Column("external_thread_id", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="sent"),
            sa.Column("last_message_at", sa.DateTime(), nullable=False),
            sa.Column("unread_reply_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_id", "external_thread_id", name="ux_email_conversation_owner_thread"),
        )
        for column in ("owner_id", "root_recruiter_email_id", "external_thread_id", "status", "last_message_at"):
            op.create_index(f"ix_email_conversations_{column}", "email_conversations", [column])

    if "email_reply_messages" not in tables:
        op.create_table(
            "email_reply_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("conversation_id", sa.Integer(), nullable=False),
            sa.Column("direction", sa.String(length=20), nullable=False, server_default="inbound"),
            sa.Column("external_message_id", sa.String(length=255), nullable=False),
            sa.Column("external_rfc_message_id", sa.String(length=500), nullable=True),
            sa.Column("in_reply_to_header", sa.Text(), nullable=True),
            sa.Column("references_header", sa.Text(), nullable=True),
            sa.Column("sender", sa.String(length=500), nullable=False, server_default=""),
            sa.Column("body", sa.Text(), nullable=False, server_default=""),
            sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
            sa.Column("received_at", sa.DateTime(), nullable=False),
            sa.Column("read_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("owner_id", "external_message_id", name="ux_email_reply_owner_message"),
        )
        for column in ("owner_id", "conversation_id", "direction", "external_message_id", "received_at"):
            op.create_index(f"ix_email_reply_messages_{column}", "email_reply_messages", [column])


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for table_name in ("email_reply_messages", "email_conversations", "email_open_events"):
        if table_name in tables:
            op.drop_table(table_name)
    if "recruiter_emails" in tables:
        recruiter_indexes = {
            index["name"] for index in sa.inspect(bind).get_indexes("recruiter_emails")
        }
        if "ix_recruiter_emails_tracking_token" in recruiter_indexes:
            op.drop_index("ix_recruiter_emails_tracking_token", table_name="recruiter_emails")
        recruiter_columns = _columns(sa.inspect(bind), "recruiter_emails")
        for name in ("open_count", "opened_at", "tracking_token"):
            if name in recruiter_columns:
                op.drop_column("recruiter_emails", name)
    if "user_settings" in tables:
        settings_columns = _columns(sa.inspect(bind), "user_settings")
        for name in ("feature_reply_inbox_enabled", "feature_email_tracking_enabled"):
            if name in settings_columns:
                op.drop_column("user_settings", name)
