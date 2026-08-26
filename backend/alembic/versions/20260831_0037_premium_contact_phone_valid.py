"""Add precomputed phone validity.

Revision ID: 20260831_0037
Revises: 20260830_0036
"""
from alembic import op
import sqlalchemy as sa
revision="20260831_0037"
down_revision="20260830_0036"
branch_labels=None
depends_on=None
def upgrade() -> None:
    columns={column["name"] for column in sa.inspect(op.get_bind()).get_columns("premium_number_contacts")}
    if "phone_is_valid" not in columns: op.add_column("premium_number_contacts",sa.Column("phone_is_valid",sa.Boolean(),nullable=False,server_default=sa.true()))
def downgrade() -> None:
    columns={column["name"] for column in sa.inspect(op.get_bind()).get_columns("premium_number_contacts")}
    if "phone_is_valid" in columns: op.drop_column("premium_number_contacts","phone_is_valid")
