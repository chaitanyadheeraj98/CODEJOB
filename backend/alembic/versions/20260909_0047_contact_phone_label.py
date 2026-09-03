"""Add label to premium_contact_phones so a number that isn't the person's own phone
line (a fax, or another non-primary line) can be recorded and shown separately instead
of competing with real phone numbers for the primary slot.

Revision ID: 20260909_0047
Revises: 20260908_0046
"""
from alembic import op
import sqlalchemy as sa

revision = "20260909_0047"
down_revision = "20260908_0046"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "label" in _columns("premium_contact_phones"):
        return
    column = sa.Column("label", sa.String(20), nullable=False, server_default="")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("premium_contact_phones") as batch:
            batch.add_column(column)
    else:
        op.add_column("premium_contact_phones", column)


def downgrade() -> None:
    if "label" not in _columns("premium_contact_phones"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("premium_contact_phones") as batch:
            batch.drop_column("label")
    else:
        op.drop_column("premium_contact_phones", "label")
