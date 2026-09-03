"""Add premium-number identity reconciliation metadata.

Revision ID: 20260829_0035
Revises: 20260828_0034
Create Date: 2026-08-29
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260829_0035"
down_revision = "20260828_0034"
branch_labels = None
depends_on = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "seen_count" not in _columns(inspector, "premium_number_contacts"):
        op.add_column(
            "premium_number_contacts",
            sa.Column("seen_count", sa.Integer(), nullable=False, server_default="1"),
        )

    lead_columns = _columns(inspector, "premium_number_leads")
    for column in (
        sa.Column("source_section", sa.String(length=20), nullable=True),
        sa.Column("block_id", sa.String(length=64), nullable=True),
        sa.Column("evidence_offset_start", sa.Integer(), nullable=True),
        sa.Column("evidence_offset_end", sa.Integer(), nullable=True),
        sa.Column("colocation_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        if column.name not in lead_columns:
            op.add_column("premium_number_leads", column)

    review_columns = _columns(inspector, "number_review_queue")
    for column in (
        sa.Column("role", sa.String(length=20), nullable=True),
        sa.Column("reason_code", sa.String(length=40), nullable=False, server_default="new_number"),
        sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
    ):
        if column.name not in review_columns:
            op.add_column("number_review_queue", column)

    inspector = sa.inspect(bind)
    review_indexes = {index["name"] for index in inspector.get_indexes("number_review_queue")}
    if "ix_number_review_queue_conflict_lookup" not in review_indexes:
        op.create_index(
            "ix_number_review_queue_conflict_lookup",
            "number_review_queue",
            ["owner_id", "normalized_phone_number", "role", "reason_code"],
            unique=False,
        )

    if not inspector.has_table("premium_number_extraction_audit"):
        op.create_table(
            "premium_number_extraction_audit",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("source_email_id", sa.Integer(), nullable=True),
            sa.Column("source_external_opportunity_id", sa.Integer(), nullable=True),
            sa.Column("raw_value", sa.String(length=120), nullable=False),
            sa.Column("normalized_value", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("stage", sa.String(length=40), nullable=False),
            sa.Column("reason", sa.String(length=160), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        audit_indexes = {
            "owner_id": "ix_pn_extract_audit_owner",
            "source_email_id": "ix_pn_extract_audit_email",
            "source_external_opportunity_id": "ix_pn_extract_audit_external",
        }
        for column, index_name in audit_indexes.items():
            op.create_index(
                index_name,
                "premium_number_extraction_audit",
                [column],
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("premium_number_extraction_audit"):
        op.drop_table("premium_number_extraction_audit")

    review_indexes = {index["name"] for index in inspector.get_indexes("number_review_queue")}
    if "ix_number_review_queue_conflict_lookup" in review_indexes:
        op.drop_index("ix_number_review_queue_conflict_lookup", table_name="number_review_queue")
    for column in ("occurrence_count", "reason_code", "role"):
        if column in _columns(sa.inspect(bind), "number_review_queue"):
            op.drop_column("number_review_queue", column)
    for column in (
        "colocation_verified",
        "evidence_offset_end",
        "evidence_offset_start",
        "block_id",
        "source_section",
    ):
        if column in _columns(sa.inspect(bind), "premium_number_leads"):
            op.drop_column("premium_number_leads", column)
    if "seen_count" in _columns(sa.inspect(bind), "premium_number_contacts"):
        op.drop_column("premium_number_contacts", "seen_count")
