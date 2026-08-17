"""Add strict candidate screening toggle and processing mode.

Revision ID: 20260723_0010
Revises: 20260722_0009
Create Date: 2026-07-23 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260723_0010"
down_revision = "20260722_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    settings_columns = (
        {column["name"] for column in inspector.get_columns("user_settings")}
        if "user_settings" in tables
        else set()
    )
    email_columns = (
        {column["name"] for column in inspector.get_columns("recruiter_emails")}
        if "recruiter_emails" in tables
        else set()
    )
    if "user_settings" in tables and "feature_strict_candidate_screening_enabled" not in settings_columns:
        op.add_column(
            "user_settings",
            sa.Column(
                "feature_strict_candidate_screening_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if "recruiter_emails" in tables and "screening_mode" not in email_columns:
        op.add_column(
            "recruiter_emails",
            sa.Column("screening_mode", sa.String(length=30), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "recruiter_emails" in tables:
        email_columns = {
            column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")
        }
        if "screening_mode" in email_columns:
            op.drop_column("recruiter_emails", "screening_mode")
    if "user_settings" in tables:
        settings_columns = {
            column["name"] for column in sa.inspect(bind).get_columns("user_settings")
        }
        if "feature_strict_candidate_screening_enabled" in settings_columns:
            op.drop_column("user_settings", "feature_strict_candidate_screening_enabled")
