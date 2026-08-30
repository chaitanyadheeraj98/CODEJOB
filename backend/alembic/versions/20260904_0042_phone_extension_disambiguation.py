"""Add phone_extension so contacts sharing a switchboard number with different
extensions can coexist instead of colliding on identity.

Revision ID: 20260904_0042
Revises: 20260904_0041
"""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0042"
down_revision = "20260904_0041"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _unique_constraints(table: str) -> set[str]:
    return {uc["name"] for uc in sa.inspect(op.get_bind()).get_unique_constraints(table) if uc.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    for table in ("premium_number_leads", "premium_number_contacts", "premium_contact_phones"):
        if "phone_extension" in _columns(table):
            continue
        column = sa.Column("phone_extension", sa.String(10), nullable=False, server_default="")
        if is_sqlite:
            with op.batch_alter_table(table) as batch:
                batch.add_column(column)
        else:
            op.add_column(table, column)

    if "ux_premium_number_contacts_owner_phone" in _unique_constraints("premium_number_contacts"):
        if is_sqlite:
            with op.batch_alter_table("premium_number_contacts") as batch:
                batch.drop_constraint("ux_premium_number_contacts_owner_phone", type_="unique")
                batch.create_unique_constraint(
                    "ux_premium_number_contacts_owner_phone",
                    ["owner_id", "normalized_phone_number", "phone_extension"],
                )
        else:
            op.drop_constraint("ux_premium_number_contacts_owner_phone", "premium_number_contacts", type_="unique")
            op.create_unique_constraint(
                "ux_premium_number_contacts_owner_phone",
                "premium_number_contacts",
                ["owner_id", "normalized_phone_number", "phone_extension"],
            )

    if "ux_premium_contact_phones_owner_phone" in _unique_constraints("premium_contact_phones"):
        if is_sqlite:
            with op.batch_alter_table("premium_contact_phones") as batch:
                batch.drop_constraint("ux_premium_contact_phones_owner_phone", type_="unique")
                batch.create_unique_constraint(
                    "ux_premium_contact_phones_owner_phone",
                    ["owner_id", "normalized_phone_number", "phone_extension"],
                )
        else:
            op.drop_constraint("ux_premium_contact_phones_owner_phone", "premium_contact_phones", type_="unique")
            op.create_unique_constraint(
                "ux_premium_contact_phones_owner_phone",
                "premium_contact_phones",
                ["owner_id", "normalized_phone_number", "phone_extension"],
            )


def downgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if "ux_premium_contact_phones_owner_phone" in _unique_constraints("premium_contact_phones"):
        if is_sqlite:
            with op.batch_alter_table("premium_contact_phones") as batch:
                batch.drop_constraint("ux_premium_contact_phones_owner_phone", type_="unique")
                batch.create_unique_constraint(
                    "ux_premium_contact_phones_owner_phone", ["owner_id", "normalized_phone_number"],
                )
        else:
            op.drop_constraint("ux_premium_contact_phones_owner_phone", "premium_contact_phones", type_="unique")
            op.create_unique_constraint(
                "ux_premium_contact_phones_owner_phone", "premium_contact_phones", ["owner_id", "normalized_phone_number"],
            )

    if "ux_premium_number_contacts_owner_phone" in _unique_constraints("premium_number_contacts"):
        if is_sqlite:
            with op.batch_alter_table("premium_number_contacts") as batch:
                batch.drop_constraint("ux_premium_number_contacts_owner_phone", type_="unique")
                batch.create_unique_constraint(
                    "ux_premium_number_contacts_owner_phone", ["owner_id", "normalized_phone_number"],
                )
        else:
            op.drop_constraint("ux_premium_number_contacts_owner_phone", "premium_number_contacts", type_="unique")
            op.create_unique_constraint(
                "ux_premium_number_contacts_owner_phone", "premium_number_contacts", ["owner_id", "normalized_phone_number"],
            )

    for table in ("premium_number_leads", "premium_number_contacts", "premium_contact_phones"):
        if "phone_extension" not in _columns(table):
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch:
                batch.drop_column("phone_extension")
        else:
            op.drop_column(table, "phone_extension")
