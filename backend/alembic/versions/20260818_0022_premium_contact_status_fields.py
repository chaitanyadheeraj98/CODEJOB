"""Add filterable source metadata and soft deletion to premium contacts.

Revision ID: 20260818_0022
Revises: 20260818_0021
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260818_0022"
down_revision = "20260818_0021"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _add_column(column: sa.Column) -> None:
    if column.name not in _columns("premium_number_contacts"):
        op.add_column("premium_number_contacts", column)


def _create_index(name: str, columns: list[str]) -> None:
    if name not in _indexes("premium_number_contacts"):
        op.create_index(name, "premium_number_contacts", columns, unique=False)


def _backfill_source_fields() -> None:
    bind = op.get_bind()
    contacts = bind.execute(
        sa.text(
            'SELECT id, is_recruiter, active_recruiter_lead_id, active_employer_lead_id, '
            'first_detected_email_id, source_email_id FROM "premium_number_contacts"'
        )
    ).mappings()
    for contact in contacts:
        active_id = (
            contact["active_recruiter_lead_id"]
            if contact["is_recruiter"] and contact["active_recruiter_lead_id"]
            else contact["active_employer_lead_id"]
        )
        lead = None
        if active_id:
            lead = bind.execute(
                sa.text(
                    'SELECT recruiter_email_id, external_opportunity_id, source_url '
                    'FROM "premium_number_leads" WHERE id=:lead_id AND contact_id=:contact_id'
                ),
                {"lead_id": active_id, "contact_id": contact["id"]},
            ).mappings().first()
        if lead and lead["external_opportunity_id"]:
            source_type = "nvoids"
            source_id = lead["external_opportunity_id"]
            source_link_url = lead["source_url"]
        elif lead and lead["recruiter_email_id"]:
            source_type = "gmail"
            source_id = lead["recruiter_email_id"]
            source_link_url = lead["source_url"]
        else:
            source_id = (
                contact["first_detected_email_id"]
                if contact["is_recruiter"]
                else contact["source_email_id"]
            )
            source_type = "gmail" if source_id else None
            source_link_url = None
        bind.execute(
            sa.text(
                'UPDATE "premium_number_contacts" SET source_type=:source_type, source_id=:source_id, '
                'source_link_url=:source_link_url WHERE id=:contact_id'
            ),
            {
                "source_type": source_type,
                "source_id": source_id,
                "source_link_url": source_link_url,
                "contact_id": contact["id"],
            },
        )


def upgrade() -> None:
    _add_column(sa.Column("source_type", sa.String(length=20), nullable=True))
    _add_column(sa.Column("source_id", sa.Integer(), nullable=True))
    _add_column(sa.Column("source_link_url", sa.String(length=1200), nullable=True))
    _add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
    _create_index("ix_premium_number_contacts_source_type", ["source_type"])
    _create_index("ix_premium_number_contacts_deleted_at", ["deleted_at"])
    _backfill_source_fields()


def downgrade() -> None:
    for index_name in (
        "ix_premium_number_contacts_deleted_at",
        "ix_premium_number_contacts_source_type",
    ):
        if index_name in _indexes("premium_number_contacts"):
            op.drop_index(index_name, table_name="premium_number_contacts")
    for column_name in ("deleted_at", "source_link_url", "source_id", "source_type"):
        if column_name in _columns("premium_number_contacts"):
            op.drop_column("premium_number_contacts", column_name)
