"""Widen text fields whose live SQLite values exceed declared VARCHAR limits.

Revision ID: 20260818_0020
Revises: 20260817_0019
Create Date: 2026-08-18

SQLite does not enforce VARCHAR lengths, so changing its physical declarations
would only rebuild large tables without changing behavior. PostgreSQL enforces
the limits and therefore receives the real type changes during cutover.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260818_0020"
down_revision = "20260817_0019"
branch_labels = None
depends_on = None


ColumnChange = tuple[str, str, sa.types.TypeEngine, sa.types.TypeEngine, int]


COLUMN_CHANGES: tuple[ColumnChange, ...] = (
    (
        "custom_skill_taxonomy_entries",
        "canonical_name",
        sa.String(length=255),
        sa.String(length=1000),
        255,
    ),
    ("external_opportunities", "company", sa.String(length=255), sa.Text(), 255),
    ("external_opportunities", "role", sa.String(length=255), sa.Text(), 255),
    ("external_opportunities", "duration", sa.String(length=255), sa.Text(), 255),
    ("external_opportunities", "rate", sa.String(length=255), sa.Text(), 255),
    ("recent_run_skipped_items", "title_or_subject", sa.String(length=500), sa.Text(), 500),
    ("recruiter_emails", "role", sa.String(length=255), sa.Text(), 255),
    ("recruiter_opportunities", "job_title", sa.String(length=255), sa.Text(), 255),
    ("recruiter_opportunities", "client", sa.String(length=255), sa.Text(), 255),
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    for table_name, column_name, existing_type, target_type, _ in COLUMN_CHANGES:
        op.alter_column(
            table_name,
            column_name,
            existing_type=existing_type,
            type_=target_type,
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    violations: list[str] = []
    for table_name, column_name, _, _, legacy_length in COLUMN_CHANGES:
        maximum = bind.execute(
            sa.text(
                f'SELECT MAX(char_length("{column_name}")) FROM "{table_name}"'
            )
        ).scalar_one()
        if maximum is not None and int(maximum) > legacy_length:
            violations.append(
                f"{table_name}.{column_name} max_length={maximum} legacy_length={legacy_length}"
            )
    if violations:
        raise RuntimeError(
            "Refusing text-field downgrade because data would be truncated: "
            + "; ".join(violations)
        )

    for table_name, column_name, existing_type, target_type, legacy_length in reversed(
        COLUMN_CHANGES
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=target_type,
            type_=existing_type,
            existing_nullable=False,
            postgresql_using=f'"{column_name}"::varchar({legacy_length})',
        )
