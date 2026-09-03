"""Add multi-identifier Premium Contacts and review actions.

Revision ID: 20260903_0040
Revises: 20260902_0039
"""
from alembic import op
import sqlalchemy as sa

revision = "20260903_0040"
down_revision = "20260902_0039"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index.get("name")}


def _index(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def upgrade() -> None:
    bind = op.get_bind()
    additions = (
        sa.Column("recruiter_email_domain", sa.String(255), nullable=False, server_default=""),
        sa.Column("employer_email_domain", sa.String(255), nullable=False, server_default=""),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    existing = _columns("premium_number_contacts")
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("premium_number_contacts") as batch:
            batch.alter_column("normalized_phone_number", existing_type=sa.String(40), nullable=True)
            for column in additions:
                if column.name not in existing:
                    batch.add_column(column)
    else:
        op.alter_column("premium_number_contacts", "normalized_phone_number", existing_type=sa.String(40), nullable=True)
        for column in additions:
            if column.name not in existing:
                op.add_column("premium_number_contacts", column)
    _index("ix_premium_number_contacts_recruiter_email_domain", "premium_number_contacts", ["recruiter_email_domain"])
    _index("ix_premium_number_contacts_employer_email_domain", "premium_number_contacts", ["employer_email_domain"])
    _index("ix_premium_number_contacts_is_favorite", "premium_number_contacts", ["is_favorite"])

    if "premium_contact_emails" not in _tables():
        op.create_table(
            "premium_contact_emails", sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("premium_contact_id", sa.Integer(), sa.ForeignKey("premium_number_contacts.id"), nullable=False),
            sa.Column("normalized_email", sa.String(255), nullable=False), sa.Column("domain", sa.String(255), nullable=False, server_default=""),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("source_email_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_id", "normalized_email", name="ux_premium_contact_emails_owner_email"),
        )
    for name, columns in (("ix_premium_contact_emails_owner_id", ["owner_id"]), ("ix_premium_contact_emails_contact_id", ["premium_contact_id"]), ("ix_premium_contact_emails_normalized_email", ["normalized_email"]), ("ix_premium_contact_emails_domain", ["domain"])):
        _index(name, "premium_contact_emails", columns)

    if "premium_contact_phones" not in _tables():
        op.create_table(
            "premium_contact_phones", sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(100), nullable=False),
            sa.Column("premium_contact_id", sa.Integer(), sa.ForeignKey("premium_number_contacts.id"), nullable=False),
            sa.Column("normalized_phone_number", sa.String(40), nullable=False),
            sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source", sa.String(50), nullable=False, server_default="unknown"), sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("owner_id", "normalized_phone_number", name="ux_premium_contact_phones_owner_phone"),
        )
    for name, columns in (("ix_premium_contact_phones_owner_id", ["owner_id"]), ("ix_premium_contact_phones_contact_id", ["premium_contact_id"]), ("ix_premium_contact_phones_normalized_phone", ["normalized_phone_number"])):
        _index(name, "premium_contact_phones", columns)

    review_columns = _columns("number_review_queue")
    for column in (
        sa.Column("target_contact_id", sa.Integer(), nullable=True),
        sa.Column("secondary_contact_id", sa.Integer(), nullable=True),
    ):
        if column.name not in review_columns:
            op.add_column("number_review_queue", column)
    _index("ix_number_review_queue_target_contact_id", "number_review_queue", ["target_contact_id"])

    if "contact_identity_actions" not in _tables():
        op.create_table(
            "contact_identity_actions", sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(100), nullable=False), sa.Column("action_type", sa.String(30), nullable=False),
            sa.Column("primary_contact_id", sa.Integer(), nullable=False), sa.Column("secondary_contact_id", sa.Integer(), nullable=True),
            sa.Column("value", sa.String(500), nullable=True), sa.Column("source", sa.String(40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    for name, columns in (("ix_contact_identity_actions_owner_id", ["owner_id"]), ("ix_contact_identity_actions_primary_contact_id", ["primary_contact_id"]), ("ix_contact_identity_actions_created_at", ["created_at"])):
        _index(name, "contact_identity_actions", columns)

    contacts = sa.table(
        "premium_number_contacts", sa.column("id"), sa.column("owner_id"), sa.column("normalized_phone_number"),
        sa.column("recruiter_email"), sa.column("recruiter_email_domain"), sa.column("employer_email"), sa.column("employer_email_domain"), sa.column("source_email_id"), sa.column("created_at"),
    )
    emails = sa.table("premium_contact_emails", sa.column("owner_id"), sa.column("premium_contact_id"), sa.column("normalized_email"), sa.column("domain"), sa.column("is_primary"), sa.column("source_email_id"), sa.column("created_at"))
    phones = sa.table("premium_contact_phones", sa.column("owner_id"), sa.column("premium_contact_id"), sa.column("normalized_phone_number"), sa.column("is_primary"), sa.column("is_verified"), sa.column("source"), sa.column("created_at"))
    for row in bind.execute(sa.select(contacts)).mappings():
        recruiter_email = str(row["recruiter_email"] or "").strip().lower()
        employer_email = str(row["employer_email"] or "").strip().lower()
        if recruiter_email:
            domain = recruiter_email.rpartition("@")[2]
            bind.execute(contacts.update().where(contacts.c.id == row["id"]).values(recruiter_email_domain=domain))
            if bind.execute(sa.select(emails.c.premium_contact_id).where(emails.c.owner_id == row["owner_id"], emails.c.normalized_email == recruiter_email)).first() is None:
                bind.execute(emails.insert().values(owner_id=row["owner_id"], premium_contact_id=row["id"], normalized_email=recruiter_email, domain=domain, is_primary=True, source_email_id=row["source_email_id"], created_at=row["created_at"]))
        if employer_email:
            bind.execute(contacts.update().where(contacts.c.id == row["id"]).values(employer_email_domain=employer_email.rpartition("@")[2]))
        if row["normalized_phone_number"]:
            if bind.execute(sa.select(phones.c.premium_contact_id).where(phones.c.owner_id == row["owner_id"], phones.c.normalized_phone_number == row["normalized_phone_number"])).first() is None:
                bind.execute(phones.insert().values(owner_id=row["owner_id"], premium_contact_id=row["id"], normalized_phone_number=row["normalized_phone_number"], is_primary=True, is_verified=True, source="migration", created_at=row["created_at"]))


def downgrade() -> None:
    for table in ("contact_identity_actions", "premium_contact_phones", "premium_contact_emails"):
        if table in _tables():
            op.drop_table(table)
    if "ix_number_review_queue_target_contact_id" in _indexes("number_review_queue"):
        op.drop_index("ix_number_review_queue_target_contact_id", table_name="number_review_queue")
    for column in ("secondary_contact_id", "target_contact_id"):
        if column in _columns("number_review_queue"):
            op.drop_column("number_review_queue", column)
    for index_name, column in (
        ("ix_premium_number_contacts_is_favorite", "is_favorite"),
        ("ix_premium_number_contacts_employer_email_domain", "employer_email_domain"),
        ("ix_premium_number_contacts_recruiter_email_domain", "recruiter_email_domain"),
    ):
        if index_name in _indexes("premium_number_contacts"):
            op.drop_index(index_name, table_name="premium_number_contacts")
        if column in _columns("premium_number_contacts"):
            op.drop_column("premium_number_contacts", column)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("premium_number_contacts") as batch:
            batch.alter_column("normalized_phone_number", existing_type=sa.String(40), nullable=False)
    else:
        op.alter_column("premium_number_contacts", "normalized_phone_number", existing_type=sa.String(40), nullable=False)
