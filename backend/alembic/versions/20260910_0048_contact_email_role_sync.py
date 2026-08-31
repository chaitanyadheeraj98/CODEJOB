"""Make premium_contact_emails the authoritative email store.

Adds a `role` column so a child email row can say which headline column it feeds
(recruiter_email or employer_email), backfills that role from the contact rows,
and adopts headline emails that have no child row at all.

Headline-vs-child OWNERSHIP MISMATCHES ARE REPORTED, NOT FIXED. A "child table
wins" rule is defensible - that store is the one carrying an enforced unique
constraint - but it is demonstrably wrong on real rows: a contact whose own name
and company domain both match its headline email can lose that email to a junk
child row left behind by a bad extraction pass. Those pairs are printed and
recorded as `identity_mismatch_report` rows in contact_identity_actions so a
human can resolve them afterwards. Same treatment for soft-deleted contacts that
still occupy a phone slot.

Steps 1-4 mutate; steps 5-6 only report. `downgrade()` reverses the schema
changes and removes this migration's report rows; the role backfill and orphan
adoption are not reversible.

Revision ID: 20260910_0048
Revises: 20260909_0047
"""
import json
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20260910_0048"
down_revision = "20260909_0047"
branch_labels = None
depends_on = None

REPORT_SOURCE = "migration_0048"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _report(
    bind,
    *,
    action_type: str,
    primary_contact_id: int,
    owner_id: str,
    payload: dict,
    secondary_contact_id: int | None = None,
) -> None:
    bind.execute(
        sa.text(
            "INSERT INTO contact_identity_actions "
            "(owner_id, action_type, primary_contact_id, secondary_contact_id, value, source, created_at) "
            "VALUES (:owner_id, :action_type, :primary_id, :secondary_id, :value, :source, :created_at)"
        ),
        {
            "owner_id": owner_id,
            "action_type": action_type,
            "primary_id": primary_contact_id,
            "secondary_id": secondary_contact_id,
            "value": json.dumps(payload, sort_keys=True),
            "source": REPORT_SOURCE,
            "created_at": datetime.now(UTC).replace(tzinfo=None),
        },
    )


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Schema: role on the child table.
    if "role" not in _columns("premium_contact_emails"):
        column = sa.Column("role", sa.String(20), nullable=False, server_default="recruiter")
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("premium_contact_emails") as batch:
                batch.add_column(column)
        else:
            op.add_column("premium_contact_emails", column)

    # 2. Schema: contact_identity_actions.value must hold a JSON snapshot (the soft-delete
    #    restore payload), not 500 chars. SQLite does not enforce VARCHAR length, but the
    #    declared type still has to match the model - test_migration_0018 walks the whole
    #    chain on SQLite and diffs the result against the declarative metadata.
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("contact_identity_actions") as batch:
            batch.alter_column("value", type_=sa.Text(), existing_nullable=True)
    else:
        op.alter_column("contact_identity_actions", "value", type_=sa.Text(), existing_nullable=True)

    # 3. Seed the role: a child row whose value matches the contact's employer_email and
    #    NOT its recruiter_email is unambiguously the employer-side address. Everything
    #    else stays "recruiter", which is the status quo - _add_email always wrote the
    #    recruiter column regardless of the role it was called with.
    bind.execute(
        sa.text(
            "UPDATE premium_contact_emails SET role = 'employer' WHERE id IN ("
            "  SELECT e.id FROM premium_contact_emails e"
            "  JOIN premium_number_contacts c ON c.id = e.premium_contact_id"
            "  WHERE c.employer_email IS NOT NULL AND c.employer_email <> ''"
            "    AND lower(c.employer_email) = e.normalized_email"
            "    AND (c.recruiter_email IS NULL OR c.recruiter_email = ''"
            "         OR lower(c.recruiter_email) <> e.normalized_email)"
            ")"
        )
    )

    # Who currently owns each email in the authoritative store?
    child_owner: dict[tuple[str, str], int] = {
        (row.owner_id, row.normalized_email): row.premium_contact_id
        for row in bind.execute(
            sa.text("SELECT owner_id, normalized_email, premium_contact_id FROM premium_contact_emails")
        )
    }
    contacts = list(
        bind.execute(
            sa.text(
                "SELECT id, owner_id, recruiter_name, owner_name, company, recruiter_email, employer_email "
                "FROM premium_number_contacts WHERE deleted_at IS NULL ORDER BY id"
            )
        )
    )
    label = {
        row.id: f"{row.recruiter_name or row.owner_name or 'Unknown'} / {row.company or 'Unknown'}"
        for row in contacts
    }

    # 4/5. Walk every headline email. Either the child table agrees (nothing to do),
    #      disagrees (report), or has no row at all (adopt). Recruiter is visited before
    #      employer so a contact holding one address in both columns adopts it as recruiter.
    adoptions: dict[tuple[str, str], tuple[int, str]] = {}
    mismatches: list[tuple] = []
    for row in contacts:
        for column, role in (("recruiter_email", "recruiter"), ("employer_email", "employer")):
            email = (getattr(row, column) or "").strip().lower()
            if not email:
                continue
            key = (row.owner_id, email)
            holder_id = child_owner.get(key)
            if holder_id == row.id:
                continue
            if holder_id is not None:
                mismatches.append((row, column, email, holder_id))
            elif key not in adoptions:
                adoptions[key] = (row.id, role)
            elif adoptions[key][0] != row.id:
                # Two contacts claim the same address and neither has a child row. The
                # first (lowest id) adopts it; the other is a real ownership dispute.
                mismatches.append((row, column, email, adoptions[key][0]))

    for (owner_id, email), (contact_id, role) in adoptions.items():
        bind.execute(
            sa.text(
                "INSERT INTO premium_contact_emails "
                "(owner_id, premium_contact_id, normalized_email, domain, is_primary, role, created_at) "
                "VALUES (:owner_id, :contact_id, :email, :domain, :is_primary, :role, :created_at)"
            ),
            {
                "owner_id": owner_id,
                "contact_id": contact_id,
                "email": email,
                "domain": email.rpartition("@")[2],
                # Bound as a real bool, not a literal 1: Postgres is_primary is BOOLEAN and
                # rejects the integer outright, while SQLite accepts it - so a SQLite-only
                # test cannot catch this. Same reason every value here is bound, not inlined.
                "is_primary": True,
                "role": role,
                "created_at": datetime.now(UTC).replace(tzinfo=None),
            },
        )
    print(f"[0048] adopted {len(adoptions)} headline email(s) into premium_contact_emails")

    print(f"[0048] {len(mismatches)} headline/child ownership mismatch(es) - REPORTED ONLY, nothing changed:")
    for row, column, email, holder_id in mismatches:
        print(
            f"[0048]   contact {row.id} ({label.get(row.id, '?')}) .{column} = {email}"
            f"  ->  owned in child table by contact {holder_id} ({label.get(holder_id, '?')})"
        )
        _report(
            bind,
            action_type="identity_mismatch_report",
            owner_id=row.owner_id,
            primary_contact_id=row.id,
            secondary_contact_id=holder_id,
            payload={
                "email": email,
                "headline_column": column,
                "headline_contact_id": row.id,
                "headline_contact": label.get(row.id, ""),
                "child_owner_contact_id": holder_id,
                "child_owner_contact": label.get(holder_id, ""),
            },
        )

    # 6. Soft-deleted contacts that still hold a phone slot. release_contact_claims frees
    #    these on every current delete path, so any survivor predates that function and is
    #    silently blocking a live contact from claiming the number.
    stale = list(
        bind.execute(
            sa.text(
                "SELECT id, owner_id, normalized_phone_number, phone_extension "
                "FROM premium_number_contacts "
                "WHERE deleted_at IS NOT NULL AND normalized_phone_number IS NOT NULL "
                "  AND normalized_phone_number <> ''"
            )
        )
    )
    print(f"[0048] {len(stale)} soft-deleted contact(s) still holding a phone slot - REPORTED ONLY:")
    for row in stale:
        print(
            f"[0048]   contact {row.id} still claims {row.normalized_phone_number} "
            f"ext '{row.phone_extension or ''}'"
        )
        _report(
            bind,
            action_type="stale_phone_claim_report",
            owner_id=row.owner_id,
            primary_contact_id=row.id,
            payload={
                "normalized_phone_number": row.normalized_phone_number,
                "phone_extension": row.phone_extension or "",
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM contact_identity_actions WHERE source = :source"), {"source": REPORT_SOURCE})
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("contact_identity_actions") as batch:
            batch.alter_column("value", type_=sa.String(500), existing_nullable=True)
    else:
        op.alter_column("contact_identity_actions", "value", type_=sa.String(500), existing_nullable=True)
    if "role" not in _columns("premium_contact_emails"):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("premium_contact_emails") as batch:
            batch.drop_column("role")
    else:
        op.drop_column("premium_contact_emails", "role")
