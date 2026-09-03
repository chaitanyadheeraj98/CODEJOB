"""Add application RTRs, interviews, risk fields, and close reason codes.

Revision ID: 20260821_0027
Revises: 20260820_0026
Create Date: 2026-08-21
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260821_0027"
down_revision = "20260820_0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "applications" in tables:
        columns = {column["name"] for column in inspector.get_columns("applications")}
        if "closed_reason_code" not in columns:
            op.add_column(
                "applications",
                sa.Column("closed_reason_code", sa.String(length=40), nullable=True),
            )

    if "recruiter_opportunities" in tables:
        columns = {column["name"] for column in inspector.get_columns("recruiter_opportunities")}
        additions = (
            ("employment_type", sa.String(length=40), False, ""),
            ("rate_amount", sa.Float(), True, None),
            ("rate_currency", sa.String(length=10), False, "USD"),
            ("rate_unit", sa.String(length=20), False, ""),
            ("contract_duration", sa.String(length=120), False, ""),
            ("relocation_required", sa.Boolean(), True, None),
            ("extension_likely", sa.String(length=20), False, "unknown"),
            ("end_client_confirmed", sa.Boolean(), False, sa.false()),
            ("job_confidence", sa.String(length=20), False, "unknown"),
        )
        for name, column_type, nullable, default in additions:
            if name not in columns:
                op.add_column(
                    "recruiter_opportunities",
                    sa.Column(
                        name,
                        column_type,
                        nullable=nullable,
                        server_default=default,
                    ),
                )

    if "premium_number_contacts" in tables:
        columns = {column["name"] for column in inspector.get_columns("premium_number_contacts")}
        additions = (
            ("recruiter_verification_level", sa.String(length=20), "unverified"),
            ("do_not_work_again", sa.Boolean(), sa.false()),
            ("do_not_work_again_reason", sa.Text(), ""),
        )
        for name, column_type, default in additions:
            if name not in columns:
                op.add_column(
                    "premium_number_contacts",
                    sa.Column(name, column_type, nullable=False, server_default=default),
                )

    if "application_rtrs" not in tables:
        op.create_table(
            "application_rtrs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("application_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="requested"),
            sa.Column("role_scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("end_client_scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("requested_at", sa.DateTime(), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("proof_attachment_id", sa.Integer(), nullable=True),
            sa.Column("proof_recruiter_email_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_application_rtrs_owner_id", "application_rtrs", ["owner_id"])
        op.create_index("ix_application_rtrs_application_id", "application_rtrs", ["application_id"])
        op.create_index("ix_application_rtrs_status", "application_rtrs", ["status"])

    if "application_interviews" not in tables:
        op.create_table(
            "application_interviews",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("application_id", sa.Integer(), nullable=False),
            sa.Column("round_type", sa.String(length=40), nullable=False, server_default="interview_1"),
            sa.Column("scheduled_at", sa.DateTime(), nullable=True),
            sa.Column("format", sa.String(length=40), nullable=False, server_default=""),
            sa.Column("interviewer_names", sa.Text(), nullable=False, server_default=""),
            sa.Column("feedback", sa.Text(), nullable=False, server_default=""),
            sa.Column("result", sa.String(length=20), nullable=False, server_default="scheduled"),
            sa.Column("follow_up_task_note", sa.Text(), nullable=False, server_default=""),
            sa.Column("deleted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_application_interviews_owner_id", "application_interviews", ["owner_id"])
        op.create_index("ix_application_interviews_application_id", "application_interviews", ["application_id"])
        op.create_index("ix_application_interviews_scheduled_at", "application_interviews", ["scheduled_at"])
        op.create_index("ix_application_interviews_deleted_at", "application_interviews", ["deleted_at"])


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("application_interviews", "application_rtrs"):
        if table in tables:
            op.drop_table(table)

    if "premium_number_contacts" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("premium_number_contacts")}
        for name in ("do_not_work_again_reason", "do_not_work_again", "recruiter_verification_level"):
            if name in columns:
                op.drop_column("premium_number_contacts", name)

    if "recruiter_opportunities" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("recruiter_opportunities")}
        for name in (
            "job_confidence",
            "end_client_confirmed",
            "extension_likely",
            "relocation_required",
            "contract_duration",
            "rate_unit",
            "rate_currency",
            "rate_amount",
            "employment_type",
        ):
            if name in columns:
                op.drop_column("recruiter_opportunities", name)

    if "applications" in tables:
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("applications")}
        if "closed_reason_code" in columns:
            op.drop_column("applications", "closed_reason_code")
