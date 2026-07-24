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
    settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
    email_columns = {column["name"] for column in inspector.get_columns("recruiter_emails")}
    if "feature_strict_candidate_screening_enabled" not in settings_columns:
        op.add_column(
            "user_settings",
            sa.Column(
                "feature_strict_candidate_screening_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    if "screening_mode" not in email_columns:
        op.add_column(
            "recruiter_emails",
            sa.Column("screening_mode", sa.String(length=30), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("recruiter_emails", "screening_mode")
    op.drop_column("user_settings", "feature_strict_candidate_screening_enabled")
