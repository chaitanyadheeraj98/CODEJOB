"""Connect application, opportunity, recruiter, resume, and analytics records.

Revision ID: 20260911_0049
Revises: 20260910_0048
"""
import re
from email.utils import parseaddr

from alembic import op
import sqlalchemy as sa

revision = "20260911_0049"
down_revision = "20260910_0048"
branch_labels = None
depends_on = None

PROMOTED_RE = re.compile(r"^appts_promoted:(\d+)$")
OPPORTUNITY_NOTE_RE = re.compile(r"^Tracked from recruiter opportunity (\d+)$")
PRODUCTIVITY_ENTITY_TYPES = {
    "approved_sent": "RecruiterEmail",
    "auto_send_failed": "RecruiterEmail",
    "failed_mapping_dismissed": "RecruiterEmail",
    "failed_mapping_marked": "RecruiterEmail",
    "needs_review_marked": "RecruiterEmail",
    "premium_contact_created": "PremiumNumberContact",
}


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table) if index.get("name")}


def _add_column(table: str, column: sa.Column) -> None:
    if column.name in _columns(table):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.add_column(column)
    else:
        op.add_column(table, column)


def _drop_column(table: str, column: str) -> None:
    if column not in _columns(table):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table) as batch:
            batch.drop_column(column)
    else:
        op.drop_column(table, column)


def _valid_source_email_id(bind, *, owner_id: str, source_email_id: int | None) -> int | None:
    if source_email_id is None:
        return None
    return bind.execute(
        sa.text("SELECT id FROM recruiter_emails WHERE owner_id = :owner_id AND id = :id"),
        {"owner_id": owner_id, "id": source_email_id},
    ).scalar_one_or_none()


def _source_email_id_for_record(bind, *, owner_id: str, record_id: str | None) -> int | None:
    if not record_id:
        return None
    return bind.execute(
        sa.text(
            "SELECT id FROM recruiter_emails "
            "WHERE owner_id = :owner_id AND record_id = :record_id "
            "ORDER BY CASE WHEN sent_at IS NULL THEN 1 ELSE 0 END, sent_at DESC, id DESC"
        ),
        {"owner_id": owner_id, "record_id": record_id},
    ).scalars().first()


def _backfill_promotions(bind) -> tuple[int, dict[int, int]]:
    count = 0
    links: dict[int, int] = {}
    rows = bind.execute(sa.text("SELECT id, owner_id, dedupe_key FROM appts_applications")).mappings()
    for row in rows:
        match = PROMOTED_RE.fullmatch(row["dedupe_key"] or "")
        if not match:
            continue
        legacy_id = int(match.group(1))
        legacy = bind.execute(
            sa.text("SELECT id FROM applications WHERE owner_id = :owner_id AND id = :id"),
            {"owner_id": row["owner_id"], "id": legacy_id},
        ).first()
        if legacy is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE applications SET promoted_to_appts_application_id = :appts_id "
                "WHERE id = :legacy_id AND owner_id = :owner_id"
            ),
            {"appts_id": row["id"], "legacy_id": legacy_id, "owner_id": row["owner_id"]},
        )
        links[legacy_id] = row["id"]
        count += 1
    return count, links


def _backfill_opportunity_links(bind) -> int:
    count = 0
    rows = bind.execute(
        sa.text("SELECT id, owner_id, manual_source_note FROM appts_applications")
    ).mappings()
    for row in rows:
        match = OPPORTUNITY_NOTE_RE.fullmatch(row["manual_source_note"] or "")
        if not match:
            continue
        opportunity = bind.execute(
            sa.text(
                "SELECT id, recruiter_number_id, source_email_id, record_id "
                "FROM recruiter_opportunities WHERE owner_id = :owner_id AND id = :id"
            ),
            {"owner_id": row["owner_id"], "id": int(match.group(1))},
        ).mappings().first()
        if opportunity is None:
            continue
        source_email_id = _valid_source_email_id(
            bind,
            owner_id=row["owner_id"],
            source_email_id=opportunity["source_email_id"],
        ) or _source_email_id_for_record(
            bind,
            owner_id=row["owner_id"],
            record_id=opportunity["record_id"],
        )
        bind.execute(
            sa.text(
                "UPDATE appts_applications SET "
                "recruiter_opportunity_id = :opportunity_id, "
                "recruiter_contact_id = :contact_id, "
                "source_recruiter_email_id = :source_email_id "
                "WHERE id = :id AND owner_id = :owner_id"
            ),
            {
                "opportunity_id": opportunity["id"],
                "contact_id": opportunity["recruiter_number_id"],
                "source_email_id": source_email_id,
                "id": row["id"],
                "owner_id": row["owner_id"],
            },
        )
        count += 1
    return count


def _backfill_promoted_links(bind, promotion_links: dict[int, int]) -> int:
    count = 0
    for legacy_id, appts_id in promotion_links.items():
        legacy = bind.execute(
            sa.text(
                "SELECT owner_id, recruiter_opportunity_id, recruiter_contact_id "
                "FROM applications WHERE id = :id"
            ),
            {"id": legacy_id},
        ).mappings().first()
        if legacy is None:
            continue
        bind.execute(
            sa.text(
                "UPDATE appts_applications SET "
                "recruiter_opportunity_id = :opportunity_id, recruiter_contact_id = :contact_id "
                "WHERE id = :id AND owner_id = :owner_id"
            ),
            {
                "opportunity_id": legacy["recruiter_opportunity_id"],
                "contact_id": legacy["recruiter_contact_id"],
                "id": appts_id,
                "owner_id": legacy["owner_id"],
            },
        )
        count += 1
    return count


def _backfill_opportunity_resumes(bind) -> tuple[int, int]:
    linked = 0
    unresolved = 0
    opportunities = bind.execute(
        sa.text(
            "SELECT id, owner_id, source_email_id, resume_file_name "
            "FROM recruiter_opportunities WHERE resume_asset_id IS NULL"
        )
    ).mappings()
    for row in opportunities:
        resume_id = None
        if row["source_email_id"] is not None:
            resume_id = bind.execute(
                sa.text(
                    "SELECT resume_asset_id FROM recruiter_emails "
                    "WHERE owner_id = :owner_id AND id = :id"
                ),
                {"owner_id": row["owner_id"], "id": row["source_email_id"]},
            ).scalar_one_or_none()
        if resume_id is None and (row["resume_file_name"] or "").strip():
            matches = list(
                bind.execute(
                    sa.text(
                        "SELECT id FROM resume_assets "
                        "WHERE owner_id = :owner_id AND file_name = :file_name ORDER BY id"
                    ),
                    {"owner_id": row["owner_id"], "file_name": row["resume_file_name"].strip()},
                ).scalars()
            )
            resume_id = matches[0] if len(matches) == 1 else None
        if resume_id is None:
            unresolved += 1
            continue
        bind.execute(
            sa.text("UPDATE recruiter_opportunities SET resume_asset_id = :resume_id WHERE id = :id"),
            {"resume_id": resume_id, "id": row["id"]},
        )
        linked += 1
    return linked, unresolved


def _backfill_recruiter_emails(bind) -> int:
    employer_domains = {
        row.owner_id: {part.strip().lower() for part in (row.employer_domains or "").split(",") if part.strip()}
        for row in bind.execute(sa.text("SELECT owner_id, employer_domains FROM user_settings"))
    }
    external_by_record = {
        (row.owner_id, row.record_id): (row.recruiter_email or "").strip().lower()
        for row in bind.execute(
            sa.text(
                "SELECT owner_id, record_id, recruiter_email FROM external_opportunities "
                "WHERE record_id IS NOT NULL AND recruiter_email IS NOT NULL AND recruiter_email <> ''"
            )
        )
    }
    count = 0
    rows = bind.execute(
        sa.text(
            "SELECT id, owner_id, source, record_id, recipient_email, sender "
            "FROM recruiter_emails WHERE resolved_recruiter_email IS NULL"
        )
    ).mappings()
    for row in rows:
        domains = employer_domains.get(row["owner_id"], set())
        candidates = [
            external_by_record.get((row["owner_id"], row["record_id"]), "") if row["source"] == "nvoids" else "",
            (row["recipient_email"] or "").strip().lower(),
            parseaddr(row["sender"] or "")[1].strip().lower(),
        ]
        normalized = next(
            (
                address for address in candidates
                if address and "@" in address and address.rpartition("@")[2] not in domains
            ),
            None,
        )
        if normalized is None:
            continue
        bind.execute(
            sa.text("UPDATE recruiter_emails SET resolved_recruiter_email = :email WHERE id = :id"),
            {"email": normalized, "id": row["id"]},
        )
        count += 1
    return count


def upgrade() -> None:
    bind = op.get_bind()
    _add_column("applications", sa.Column("promoted_to_appts_application_id", sa.Integer(), nullable=True))
    if "ix_applications_promoted_to_appts_application_id" not in _indexes("applications"):
        op.create_index(
            "ix_applications_promoted_to_appts_application_id",
            "applications",
            ["promoted_to_appts_application_id"],
        )
    _add_column("recruiter_opportunities", sa.Column("resume_asset_id", sa.Integer(), nullable=True))
    if "ix_recruiter_opportunities_resume_asset_id" not in _indexes("recruiter_opportunities"):
        op.create_index(
            "ix_recruiter_opportunities_resume_asset_id",
            "recruiter_opportunities",
            ["resume_asset_id"],
        )
    _add_column(
        "productivity_events",
        sa.Column("entity_type", sa.String(40), nullable=False, server_default=""),
    )
    if "ix_productivity_events_entity_type" not in _indexes("productivity_events"):
        op.create_index("ix_productivity_events_entity_type", "productivity_events", ["entity_type"])

    promoted_count, promotion_links = _backfill_promotions(bind)
    opportunity_count = _backfill_opportunity_links(bind)
    promoted_link_count = _backfill_promoted_links(bind, promotion_links)
    resume_count, unresolved_resume_count = _backfill_opportunity_resumes(bind)
    identity_count = _backfill_recruiter_emails(bind)
    for event_type, entity_type in PRODUCTIVITY_ENTITY_TYPES.items():
        bind.execute(
            sa.text(
                "UPDATE productivity_events SET entity_type = :entity_type "
                "WHERE event_type = :event_type AND entity_type = ''"
            ),
            {"entity_type": entity_type, "event_type": event_type},
        )
    print(f"[0049] linked {promoted_count} promoted legacy application(s)")
    print(f"[0049] repaired {opportunity_count} opportunity-tracked application(s)")
    print(f"[0049] carried links to {promoted_link_count} promoted AppTS application(s)")
    print(
        f"[0049] linked resumes to {resume_count} opportunity row(s); "
        f"{unresolved_resume_count} remain unresolved"
    )
    print(f"[0049] normalized recruiter identity on {identity_count} email row(s)")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE appts_applications SET source_recruiter_email_id = NULL "
            "WHERE manual_source_note LIKE 'Tracked from recruiter opportunity %'"
        )
    )
    if "ix_productivity_events_entity_type" in _indexes("productivity_events"):
        op.drop_index("ix_productivity_events_entity_type", table_name="productivity_events")
    _drop_column("productivity_events", "entity_type")
    if "ix_recruiter_opportunities_resume_asset_id" in _indexes("recruiter_opportunities"):
        op.drop_index("ix_recruiter_opportunities_resume_asset_id", table_name="recruiter_opportunities")
    _drop_column("recruiter_opportunities", "resume_asset_id")
    if "ix_applications_promoted_to_appts_application_id" in _indexes("applications"):
        op.drop_index("ix_applications_promoted_to_appts_application_id", table_name="applications")
    _drop_column("applications", "promoted_to_appts_application_id")
