"""Add multi-value preferred and default employer CC settings.

Revision ID: 20260810_0014
Revises: 20260805_0013
Create Date: 2026-08-10 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260810_0014"
down_revision = "20260805_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "user_settings" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("user_settings")}
    if "preferred_employer_cc_emails" not in columns:
        op.add_column(
            "user_settings",
            sa.Column("preferred_employer_cc_emails", sa.Text(), nullable=False, server_default=""),
        )
    if "default_employer_cc_emails" not in columns:
        op.add_column(
            "user_settings",
            sa.Column("default_employer_cc_emails", sa.Text(), nullable=False, server_default=""),
        )
    op.execute(
        "UPDATE user_settings "
        "SET preferred_employer_cc_emails = lower(trim(preferred_employer_cc_email)) "
        "WHERE preferred_employer_cc_emails = '' AND trim(preferred_employer_cc_email) <> ''"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "user_settings" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("user_settings")}
    for name in ("default_employer_cc_emails", "preferred_employer_cc_emails"):
        if name in columns:
            op.drop_column("user_settings", name)
