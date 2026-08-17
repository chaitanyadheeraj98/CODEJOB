"""Add vendor company, end client, implementation partner, domain, and interview type fields.

Revision ID: 20260815_0016
Revises: 20260814_0015
Create Date: 2026-08-15 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260815_0016"
down_revision = "20260814_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")}

    if "company" not in columns:
        op.add_column("recruiter_emails", sa.Column("company", sa.String(length=255), nullable=True))
        op.create_index("ix_recruiter_emails_company", "recruiter_emails", ["company"])
    if "end_client" not in columns:
        op.add_column("recruiter_emails", sa.Column("end_client", sa.String(length=255), nullable=True))
        op.create_index("ix_recruiter_emails_end_client", "recruiter_emails", ["end_client"])
    if "implementation_partner" not in columns:
        op.add_column("recruiter_emails", sa.Column("implementation_partner", sa.String(length=255), nullable=True))
        op.create_index(
            "ix_recruiter_emails_implementation_partner", "recruiter_emails", ["implementation_partner"]
        )
    if "domain" not in columns:
        op.add_column("recruiter_emails", sa.Column("domain", sa.String(length=120), nullable=True))
        op.create_index("ix_recruiter_emails_domain", "recruiter_emails", ["domain"])
    if "domain_confidence" not in columns:
        op.add_column("recruiter_emails", sa.Column("domain_confidence", sa.String(length=20), nullable=True))
    if "interview_type" not in columns:
        op.add_column("recruiter_emails", sa.Column("interview_type", sa.String(length=255), nullable=True))
        op.create_index("ix_recruiter_emails_interview_type", "recruiter_emails", ["interview_type"])


def downgrade() -> None:
    bind = op.get_bind()
    if "recruiter_emails" not in sa.inspect(bind).get_table_names():
        return
    columns = {column["name"] for column in sa.inspect(bind).get_columns("recruiter_emails")}

    if "interview_type" in columns:
        op.drop_index("ix_recruiter_emails_interview_type", table_name="recruiter_emails")
        op.drop_column("recruiter_emails", "interview_type")
    if "domain_confidence" in columns:
        op.drop_column("recruiter_emails", "domain_confidence")
    if "domain" in columns:
        op.drop_index("ix_recruiter_emails_domain", table_name="recruiter_emails")
        op.drop_column("recruiter_emails", "domain")
    if "implementation_partner" in columns:
        op.drop_index("ix_recruiter_emails_implementation_partner", table_name="recruiter_emails")
        op.drop_column("recruiter_emails", "implementation_partner")
    if "end_client" in columns:
        op.drop_index("ix_recruiter_emails_end_client", table_name="recruiter_emails")
        op.drop_column("recruiter_emails", "end_client")
    if "company" in columns:
        op.drop_index("ix_recruiter_emails_company", table_name="recruiter_emails")
        op.drop_column("recruiter_emails", "company")
