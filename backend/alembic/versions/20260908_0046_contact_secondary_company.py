"""Add secondary_company to premium_number_contacts so a phone number seen under
a second company (a sister company, not a data-entry mistake) can be kept
alongside the primary company instead of silently overwriting or dropping it.

Revision ID: 20260908_0046
Revises: 20260907_0045
"""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0046"
down_revision = "20260907_0045"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "secondary_company" in _columns("premium_number_contacts"):
        return
    column = sa.Column("secondary_company", sa.String(255), nullable=False, server_default="")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("premium_number_contacts") as batch:
            batch.add_column(column)
    else:
        op.add_column("premium_number_contacts", column)


def downgrade() -> None:
    if "secondary_company" not in _columns("premium_number_contacts"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("premium_number_contacts") as batch:
            batch.drop_column("secondary_company")
    else:
        op.drop_column("premium_number_contacts", "secondary_company")
