"""Unify premium contacts and add versioned review/source fields.

Revision ID: 20260818_0021
Revises: 20260818_0020
Create Date: 2026-08-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260818_0021"
down_revision = "20260818_0020"
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


def _add_column(table_name: str, column: sa.Column) -> None:
    if column.name not in _columns(table_name):
        if op.get_bind().dialect.name == "sqlite" and column.foreign_keys:
            with op.batch_alter_table(table_name) as batch_op:
                batch_op.add_column(column)
        else:
            op.add_column(table_name, column)


def _create_index(name: str, table_name: str, columns: list[str]) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=False)


def _create_contacts_table() -> None:
    if "premium_number_contacts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "premium_number_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("normalized_phone_number", sa.String(length=40), nullable=False),
        sa.Column("display_phone_number", sa.String(length=80), nullable=False),
        sa.Column("is_recruiter", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_employer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recruiter_name", sa.String(length=255), nullable=False, server_default="Unknown"),
        sa.Column("designation", sa.String(length=255), nullable=False, server_default="Unknown"),
        sa.Column("recruiter_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("owner_name", sa.String(length=255), nullable=False, server_default="Unknown"),
        sa.Column("company", sa.String(length=255), nullable=False, server_default="Unknown"),
        sa.Column("first_detected_email_id", sa.Integer(), nullable=True),
        sa.Column("source_email_id", sa.Integer(), nullable=True),
        sa.Column(
            "active_recruiter_lead_id",
            sa.Integer(),
            sa.ForeignKey(
                "premium_number_leads.id",
                name="fk_premium_number_contacts_active_recruiter_lead",
            ),
            nullable=True,
        ),
        sa.Column(
            "active_employer_lead_id",
            sa.Integer(),
            sa.ForeignKey(
                "premium_number_leads.id",
                name="fk_premium_number_contacts_active_employer_lead",
            ),
            nullable=True,
        ),
        sa.Column("linkedin_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint(
            "owner_id",
            "normalized_phone_number",
            name="ux_premium_number_contacts_owner_phone",
        ),
    )
    op.create_index("ix_premium_number_contacts_id", "premium_number_contacts", ["id"])
    op.create_index("ix_premium_number_contacts_owner_id", "premium_number_contacts", ["owner_id"])
    op.create_index(
        "ix_premium_number_contacts_normalized_phone_number",
        "premium_number_contacts",
        ["normalized_phone_number"],
    )


def _sync_contact_sequence(bind) -> None:
    if bind.dialect.name != "postgresql":
        return
    sequence = bind.execute(
        sa.text("SELECT pg_get_serial_sequence('premium_number_contacts', 'id')")
    ).scalar()
    if sequence:
        bind.execute(
            sa.text(
                'SELECT setval(CAST(:sequence AS regclass), '
                'COALESCE((SELECT MAX(id) FROM "premium_number_contacts"), 1), '
                '(SELECT MAX(id) IS NOT NULL FROM "premium_number_contacts"))'
            ),
            {"sequence": sequence},
        )


def _copy_legacy_contacts() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "recruiter_numbers" in tables:
        bind.execute(
            sa.text(
                'INSERT INTO "premium_number_contacts" '
                "(id, owner_id, normalized_phone_number, display_phone_number, is_recruiter, is_employer, "
                "recruiter_name, designation, recruiter_email, owner_name, company, first_detected_email_id, "
                "source_email_id, active_recruiter_lead_id, active_employer_lead_id, linkedin_url, created_at, updated_at) "
                "SELECT id, owner_id, normalized_phone_number, display_phone_number, :is_recruiter, :is_employer, "
                "recruiter_name, designation, recruiter_email, 'Unknown', company, first_detected_email_id, NULL, "
                "NULL, NULL, '', created_at, updated_at "
                'FROM "recruiter_numbers" '
                'WHERE id NOT IN (SELECT id FROM "premium_number_contacts")'
            ),
            {"is_recruiter": True, "is_employer": False},
        )

        # Recruiter ids are inserted explicitly to preserve opportunity FKs.
        # Advance PostgreSQL's sequence before employer-only rows request ids.
        _sync_contact_sequence(bind)

    if "employer_numbers" in tables:
        employers = bind.execute(sa.text('SELECT * FROM "employer_numbers" ORDER BY id')).mappings()
        for row in employers:
            existing = bind.execute(
                sa.text(
                    'SELECT id FROM "premium_number_contacts" '
                    "WHERE owner_id=:owner_id AND normalized_phone_number=:phone"
                ),
                {"owner_id": row["owner_id"], "phone": row["normalized_phone_number"]},
            ).mappings().first()
            if existing:
                bind.execute(
                    sa.text(
                        'UPDATE "premium_number_contacts" SET is_employer=:is_employer, owner_name=:owner_name, '
                        "source_email_id=:source_email_id, "
                        "company=COALESCE(NULLIF(company, 'Unknown'), :company) WHERE id=:id"
                    ),
                    {
                        "is_employer": True,
                        "owner_name": row["owner_name"],
                        "source_email_id": row["source_email_id"],
                        "company": row["company"],
                        "id": existing["id"],
                    },
                )
                continue
            bind.execute(
                sa.text(
                    'INSERT INTO "premium_number_contacts" '
                    "(owner_id, normalized_phone_number, display_phone_number, is_recruiter, is_employer, "
                    "recruiter_name, designation, recruiter_email, owner_name, company, first_detected_email_id, "
                    "source_email_id, active_recruiter_lead_id, active_employer_lead_id, linkedin_url, created_at, updated_at) "
                    "VALUES (:owner_id, :phone, :display, :is_recruiter, :is_employer, 'Unknown', 'Unknown', '', "
                    ":owner_name, :company, NULL, :source_email_id, NULL, NULL, '', :created_at, :updated_at)"
                ),
                {
                    "owner_id": row["owner_id"],
                    "phone": row["normalized_phone_number"],
                    "display": row["display_phone_number"],
                    "is_recruiter": False,
                    "is_employer": True,
                    "owner_name": row["owner_name"],
                    "company": row["company"],
                    "source_email_id": row["source_email_id"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                },
            )

    _sync_contact_sequence(bind)


def upgrade() -> None:
    _create_contacts_table()

    for column in (
        sa.Column(
            "external_opportunity_id",
            sa.Integer(),
            sa.ForeignKey(
                "external_opportunities.id",
                name="fk_premium_number_leads_external_opportunity",
            ),
            nullable=True,
        ),
        sa.Column(
            "contact_id",
            sa.Integer(),
            sa.ForeignKey(
                "premium_number_contacts.id",
                name="fk_premium_number_leads_contact",
            ),
            nullable=True,
        ),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="recruiter"),
        sa.Column("extraction_source", sa.String(length=50), nullable=False, server_default="ai"),
        sa.Column("contact_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(length=1200), nullable=True),
    ):
        _add_column("premium_number_leads", column)

    for name, columns in (
        ("ix_premium_number_leads_external_opportunity_id", ["external_opportunity_id"]),
        ("ix_premium_number_leads_contact_id", ["contact_id"]),
    ):
        _create_index(name, "premium_number_leads", columns)

    for column in (
        sa.Column(
            "source_external_opportunity_id",
            sa.Integer(),
            sa.ForeignKey(
                "external_opportunities.id",
                name="fk_number_review_queue_external_opportunity",
            ),
            nullable=True,
        ),
        sa.Column(
            "source_lead_id",
            sa.Integer(),
            sa.ForeignKey(
                "premium_number_leads.id",
                name="fk_number_review_queue_source_lead",
            ),
            nullable=True,
        ),
        sa.Column("contact_email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("contact_type", sa.String(length=40), nullable=False, server_default="unknown"),
        sa.Column("recruiter_relevance_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("relevance_reason", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("extraction_source", sa.String(length=50), nullable=False, server_default="ai"),
        sa.Column("scored_with", sa.String(length=20), nullable=False, server_default="legacy"),
    ):
        _add_column("number_review_queue", column)

    for name, columns in (
        ("ix_number_review_queue_source_external_opportunity_id", ["source_external_opportunity_id"]),
        ("ix_number_review_queue_source_lead_id", ["source_lead_id"]),
    ):
        _create_index(name, "number_review_queue", columns)

    bind = op.get_bind()
    if not next(
        column for column in sa.inspect(bind).get_columns("premium_number_leads") if column["name"] == "recruiter_email_id"
    )["nullable"]:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("premium_number_leads") as batch_op:
                batch_op.alter_column("recruiter_email_id", existing_type=sa.Integer(), nullable=True)
        else:
            op.alter_column("premium_number_leads", "recruiter_email_id", existing_type=sa.Integer(), nullable=True)

    if not next(
        column for column in sa.inspect(bind).get_columns("number_review_queue") if column["name"] == "source_email_id"
    )["nullable"]:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("number_review_queue") as batch_op:
                batch_op.alter_column("source_email_id", existing_type=sa.Integer(), nullable=True)
        else:
            op.alter_column("number_review_queue", "source_email_id", existing_type=sa.Integer(), nullable=True)

    opportunity_columns = _columns("recruiter_opportunities")
    if "client" in opportunity_columns and "end_client" not in opportunity_columns:
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("recruiter_opportunities") as batch_op:
                batch_op.alter_column("client", new_column_name="end_client", existing_type=sa.String(length=255))
        else:
            op.alter_column("recruiter_opportunities", "client", new_column_name="end_client", existing_type=sa.Text())

    for column in (
        sa.Column("resume_file_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("implementation_partner", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("prime_vendor", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("domain", sa.String(length=255), nullable=False, server_default=""),
    ):
        _add_column("recruiter_opportunities", column)

    _copy_legacy_contacts()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.execute(sa.text('SELECT 1 FROM "premium_number_leads" WHERE recruiter_email_id IS NULL LIMIT 1')).first():
        raise RuntimeError("Refusing downgrade while Nvoids premium-number leads exist")
    if bind.execute(sa.text('SELECT 1 FROM "number_review_queue" WHERE source_email_id IS NULL LIMIT 1')).first():
        raise RuntimeError("Refusing downgrade while Nvoids number-review rows exist")

    for column_name in ("domain", "prime_vendor", "implementation_partner", "resume_file_name"):
        if column_name in _columns("recruiter_opportunities"):
            op.drop_column("recruiter_opportunities", column_name)
    if "end_client" in _columns("recruiter_opportunities") and "client" not in _columns("recruiter_opportunities"):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("recruiter_opportunities") as batch_op:
                batch_op.alter_column("end_client", new_column_name="client", existing_type=sa.String(length=255))
        else:
            op.alter_column("recruiter_opportunities", "end_client", new_column_name="client", existing_type=sa.Text())

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch_op:
            batch_op.alter_column("source_email_id", existing_type=sa.Integer(), nullable=False)
        with op.batch_alter_table("premium_number_leads") as batch_op:
            batch_op.alter_column("recruiter_email_id", existing_type=sa.Integer(), nullable=False)
    else:
        op.alter_column("number_review_queue", "source_email_id", existing_type=sa.Integer(), nullable=False)
        op.alter_column("premium_number_leads", "recruiter_email_id", existing_type=sa.Integer(), nullable=False)

    for index_name, table_name in (
        ("ix_number_review_queue_source_lead_id", "number_review_queue"),
        ("ix_number_review_queue_source_external_opportunity_id", "number_review_queue"),
        ("ix_premium_number_leads_contact_id", "premium_number_leads"),
        ("ix_premium_number_leads_external_opportunity_id", "premium_number_leads"),
    ):
        if index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)

    review_columns = (
        "scored_with",
        "extraction_source",
        "relevance_reason",
        "recruiter_relevance_score",
        "contact_type",
        "contact_email",
        "source_lead_id",
        "source_external_opportunity_id",
    )
    lead_columns = (
        "source_url",
        "contact_email",
        "extraction_source",
        "role",
        "contact_id",
        "external_opportunity_id",
    )
    if bind.dialect.name == "sqlite":
        review_foreign_keys = {
            foreign_key["name"]
            for foreign_key in sa.inspect(bind).get_foreign_keys("number_review_queue")
            if foreign_key.get("name")
        }
        with op.batch_alter_table("number_review_queue") as batch_op:
            for constraint_name in (
                "fk_number_review_queue_source_lead",
                "fk_number_review_queue_external_opportunity",
            ):
                if constraint_name in review_foreign_keys:
                    batch_op.drop_constraint(constraint_name, type_="foreignkey")
            for column_name in review_columns:
                if column_name in _columns("number_review_queue"):
                    batch_op.drop_column(column_name)

        lead_foreign_keys = {
            foreign_key["name"]
            for foreign_key in sa.inspect(bind).get_foreign_keys("premium_number_leads")
            if foreign_key.get("name")
        }
        with op.batch_alter_table("premium_number_leads") as batch_op:
            for constraint_name in (
                "fk_premium_number_leads_contact",
                "fk_premium_number_leads_external_opportunity",
            ):
                if constraint_name in lead_foreign_keys:
                    batch_op.drop_constraint(constraint_name, type_="foreignkey")
            for column_name in lead_columns:
                if column_name in _columns("premium_number_leads"):
                    batch_op.drop_column(column_name)
    else:
        for column_name in review_columns:
            if column_name in _columns("number_review_queue"):
                op.drop_column("number_review_queue", column_name)
        for column_name in lead_columns:
            if column_name in _columns("premium_number_leads"):
                op.drop_column("premium_number_leads", column_name)
    if "premium_number_contacts" in sa.inspect(bind).get_table_names():
        op.drop_table("premium_number_contacts")
