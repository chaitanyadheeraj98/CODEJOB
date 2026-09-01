"""Enforce the links 0049 populated, so a deleted opportunity, contact, or resume
cannot leave an application pointing at a row that no longer exists.

Nullable links get ondelete=SET NULL. email_conversations.root_recruiter_email_id
is NOT NULL, so SET NULL is impossible there; it gets RESTRICT instead, which is
free today because recruiter_emails has no delete path in the codebase.

Revision ID: 20260912_0050
Revises: 20260911_0049
"""
from alembic import op
import sqlalchemy as sa

revision = "20260912_0050"
down_revision = "20260911_0049"
branch_labels = None
depends_on = None

DANGLING_ABORT_RATIO = 0.05

# table -> list of (column, target_table, constraint_name, ondelete)
NULLABLE_LINKS: dict[str, list[tuple[str, str, str, str]]] = {
    "applications": [
        ("recruiter_opportunity_id", "recruiter_opportunities", "fk_applications_recruiter_opportunity", "SET NULL"),
        ("recruiter_contact_id", "premium_number_contacts", "fk_applications_recruiter_contact", "SET NULL"),
    ],
    "appts_applications": [
        ("recruiter_opportunity_id", "recruiter_opportunities", "fk_appts_applications_recruiter_opportunity", "SET NULL"),
        ("recruiter_contact_id", "premium_number_contacts", "fk_appts_applications_recruiter_contact", "SET NULL"),
        ("source_recruiter_email_id", "recruiter_emails", "fk_appts_applications_source_recruiter_email", "SET NULL"),
    ],
    "recruiter_opportunities": [
        ("resume_asset_id", "resume_assets", "fk_recruiter_opportunities_resume_asset", "SET NULL"),
    ],
}

RESTRICT_LINKS: dict[str, list[tuple[str, str, str, str]]] = {
    "email_conversations": [
        ("root_recruiter_email_id", "recruiter_emails", "fk_email_conversations_root_recruiter_email", "RESTRICT"),
    ],
}

INDEXES: list[tuple[str, str, list[str]]] = [
    (
        "ix_appts_applications_owner_contact_created",
        "appts_applications",
        ["owner_id", "resolved_recruiter_contact_id", "created_at"],
    ),
    (
        "ix_email_reply_messages_conversation_direction_received",
        "email_reply_messages",
        ["conversation_id", "direction", "received_at"],
    ),
    (
        "ix_recruiter_emails_owner_resolved_email_sent",
        "recruiter_emails",
        ["owner_id", "resolved_recruiter_email", "sent_at"],
    ),
]


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index.get("name")}


def _foreign_key_names(table: str) -> set[str]:
    return {fk.get("name") for fk in sa.inspect(op.get_bind()).get_foreign_keys(table) if fk.get("name")}


def _row_count(bind, table: str) -> int:
    return bind.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar_one()


def _dangling_count(bind, *, table: str, column: str, target: str) -> int:
    return bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {table} AS child "
            f"WHERE child.{column} IS NOT NULL "
            f"AND NOT EXISTS (SELECT 1 FROM {target} AS parent WHERE parent.id = child.{column})"
        )
    ).scalar_one()


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Survey before mutating anything. A large dangling count means 0049 misfired,
    #    and that must be investigated rather than silently discarded.
    plan: list[tuple[str, str, str, int]] = []
    for table, links in NULLABLE_LINKS.items():
        total = _row_count(bind, table)
        for column, target, _name, _ondelete in links:
            dangling = _dangling_count(bind, table=table, column=column, target=target)
            print(f"[0050] {table}.{column}: {dangling} dangling of {total} row(s)")
            if dangling and total and (dangling / total) > DANGLING_ABORT_RATIO:
                raise RuntimeError(
                    f"[0050] {table}.{column} has {dangling} dangling reference(s) out of {total} rows "
                    f"(> {DANGLING_ABORT_RATIO:.0%}). Migration 0049 likely misfired; investigate before "
                    "adding constraints."
                )
            if dangling:
                plan.append((table, column, target, dangling))

    for table, links in RESTRICT_LINKS.items():
        total = _row_count(bind, table)
        for column, target, _name, _ondelete in links:
            dangling = _dangling_count(bind, table=table, column=column, target=target)
            print(f"[0050] {table}.{column}: {dangling} dangling of {total} row(s) (NOT NULL, cannot be cleaned)")
            if dangling:
                raise RuntimeError(
                    f"[0050] {table}.{column} has {dangling} dangling reference(s) and is NOT NULL, so it "
                    "cannot be repaired automatically. Resolve these rows before re-running."
                )

    # 2. Pre-clean the survivors, now that we know the damage is small.
    for table, column, target, dangling in plan:
        bind.execute(
            sa.text(
                f"UPDATE {table} SET {column} = NULL WHERE {column} IS NOT NULL "
                f"AND {column} NOT IN (SELECT id FROM {target})"
            )
        )
        print(f"[0050] nulled {dangling} dangling {table}.{column} value(s)")

    # 3. Add the constraints, one table rebuild each on SQLite.
    for table, links in list(NULLABLE_LINKS.items()) + list(RESTRICT_LINKS.items()):
        pending = [link for link in links if link[2] not in _foreign_key_names(table)]
        if not pending:
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch:
                for column, target, name, ondelete in pending:
                    batch.create_foreign_key(name, target, [column], ["id"], ondelete=ondelete)
        else:
            for column, target, name, ondelete in pending:
                op.create_foreign_key(name, table, target, [column], ["id"], ondelete=ondelete)

    # 4. Indexes the per-recruiter and outcome aggregates will need.
    for name, table, columns in INDEXES:
        if name not in _indexes(table):
            op.create_index(name, table, columns)



def downgrade() -> None:
    bind = op.get_bind()
    for name, table, _columns in INDEXES:
        if name in _indexes(table):
            op.drop_index(name, table_name=table)
    for table, links in list(NULLABLE_LINKS.items()) + list(RESTRICT_LINKS.items()):
        present = [link for link in links if link[2] in _foreign_key_names(table)]
        if not present:
            continue
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table) as batch:
                for _column, _target, name, _ondelete in present:
                    batch.drop_constraint(name, type_="foreignkey")
        else:
            for _column, _target, name, _ondelete in present:
                op.drop_constraint(name, table, type_="foreignkey")
