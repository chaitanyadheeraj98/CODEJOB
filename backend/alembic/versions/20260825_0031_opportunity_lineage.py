"""Add source-agnostic opportunity lineage history.

Revision ID: 20260825_0031
Revises: 20260824_0030
Create Date: 2026-08-25
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa


revision = "20260825_0031"
down_revision = "20260824_0030"
branch_labels = None
depends_on = None


lineages = sa.table(
    "opportunity_lineages",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("origin_type"),
    sa.column("recruiter_opportunity_id"),
    sa.column("current_status"),
    sa.column("created_at"),
    sa.column("closed_at"),
)
source_references = sa.table(
    "opportunity_source_references",
    sa.column("owner_id"),
    sa.column("lineage_id"),
    sa.column("source_type"),
    sa.column("external_id"),
    sa.column("source_url"),
    sa.column("first_seen_at"),
    sa.column("last_seen_at"),
)
lifecycle_events = sa.table(
    "opportunity_lifecycle_events",
    sa.column("owner_id"),
    sa.column("lineage_id"),
    sa.column("event_type"),
    sa.column("occurred_at"),
    sa.column("actor"),
    sa.column("process_name"),
    sa.column("related_record_type"),
    sa.column("related_record_id"),
    sa.column("note"),
    sa.column("metadata_json"),
    sa.column("created_at"),
)
recruiter_opportunities = sa.table(
    "recruiter_opportunities",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("source_type"),
    sa.column("source_email_id"),
    sa.column("external_opportunity_id"),
    sa.column("source_url"),
    sa.column("gmail_open_url"),
    sa.column("status"),
    sa.column("created_at"),
)
review_queue = sa.table(
    "number_review_queue",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("lineage_id"),
    sa.column("source_email_id"),
    sa.column("source_external_opportunity_id"),
    sa.column("gmail_open_url"),
    sa.column("state"),
    sa.column("created_at"),
)


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _foreign_keys(table_name: str) -> set[str]:
    return {
        foreign_key["name"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
        if foreign_key.get("name")
    }


def _create_index(name: str, table_name: str, columns: list[str], *, unique: bool = False) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def _create_lineage_tables() -> None:
    tables = _table_names()
    if "opportunity_lineages" not in tables:
        op.create_table(
            "opportunity_lineages",
            sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("origin_type", sa.String(length=20), nullable=False),
            sa.Column("recruiter_opportunity_id", sa.Integer(), nullable=True),
            sa.Column("current_status", sa.String(length=20), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["recruiter_opportunity_id"],
                ["recruiter_opportunities.id"],
                name="fk_opportunity_lineage_recruiter_opportunity",
            ),
        )
    for name, columns, unique in (
        ("ix_opportunity_lineages_owner_id", ["owner_id"], False),
        ("ix_opportunity_lineages_origin_type", ["origin_type"], False),
        (
            "ix_opportunity_lineages_recruiter_opportunity_id",
            ["recruiter_opportunity_id"],
            True,
        ),
    ):
        _create_index(name, "opportunity_lineages", columns, unique=unique)

    tables = _table_names()
    if "opportunity_source_references" not in tables:
        op.create_table(
            "opportunity_source_references",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("lineage_id", sa.String(length=36), nullable=False),
            sa.Column("source_type", sa.String(length=20), nullable=False),
            sa.Column("external_id", sa.String(length=255), nullable=False),
            sa.Column("source_url", sa.String(length=1200), nullable=False),
            sa.Column("first_seen_at", sa.DateTime(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["lineage_id"],
                ["opportunity_lineages.id"],
                name="fk_source_reference_lineage",
            ),
            sa.UniqueConstraint(
                "lineage_id",
                "source_type",
                "external_id",
                name="ux_opportunity_source_reference",
            ),
        )
    for name, columns in (
        ("ix_opportunity_source_references_id", ["id"]),
        ("ix_opportunity_source_references_owner_id", ["owner_id"]),
        ("ix_opportunity_source_references_lineage_id", ["lineage_id"]),
        ("ix_opportunity_source_references_source_type", ["source_type"]),
    ):
        _create_index(name, "opportunity_source_references", columns)

    tables = _table_names()
    if "opportunity_lifecycle_events" not in tables:
        op.create_table(
            "opportunity_lifecycle_events",
            sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("lineage_id", sa.String(length=36), nullable=False),
            sa.Column("event_type", sa.String(length=40), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.Column("actor", sa.String(length=20), nullable=False),
            sa.Column("process_name", sa.String(length=60), nullable=False),
            sa.Column("related_record_type", sa.String(length=40), nullable=False),
            sa.Column("related_record_id", sa.Integer(), nullable=True),
            sa.Column("note", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["lineage_id"],
                ["opportunity_lineages.id"],
                name="fk_lifecycle_event_lineage",
            ),
        )
    for name, columns in (
        ("ix_opportunity_lifecycle_events_id", ["id"]),
        ("ix_opportunity_lifecycle_events_owner_id", ["owner_id"]),
        ("ix_opportunity_lifecycle_events_lineage_id", ["lineage_id"]),
        ("ix_opportunity_lifecycle_events_event_type", ["event_type"]),
        ("ix_opportunity_lifecycle_events_occurred_at", ["occurred_at"]),
    ):
        _create_index(name, "opportunity_lifecycle_events", columns)


def _add_review_lineage_column() -> None:
    bind = op.get_bind()
    column_missing = "lineage_id" not in _columns("number_review_queue")
    foreign_key_missing = "fk_number_review_queue_lineage" not in _foreign_keys(
        "number_review_queue"
    )
    if bind.dialect.name == "sqlite" and (column_missing or foreign_key_missing):
        with op.batch_alter_table("number_review_queue") as batch_op:
            if column_missing:
                batch_op.add_column(sa.Column("lineage_id", sa.String(length=36), nullable=True))
            if foreign_key_missing:
                batch_op.create_foreign_key(
                    "fk_number_review_queue_lineage",
                    "opportunity_lineages",
                    ["lineage_id"],
                    ["id"],
                )
    else:
        if column_missing:
            op.add_column(
                "number_review_queue",
                sa.Column("lineage_id", sa.String(length=36), nullable=True),
            )
        if foreign_key_missing:
            op.create_foreign_key(
                "fk_number_review_queue_lineage",
                "number_review_queue",
                "opportunity_lineages",
                ["lineage_id"],
                ["id"],
            )
    _create_index(
        "ix_number_review_queue_lineage_id",
        "number_review_queue",
        ["lineage_id"],
    )


def _backfill_promoted_opportunities() -> None:
    bind = op.get_bind()
    existing_opportunity_ids = {
        value
        for value in bind.execute(
            sa.select(lineages.c.recruiter_opportunity_id).where(
                lineages.c.recruiter_opportunity_id.is_not(None)
            )
        ).scalars()
    }
    rows = bind.execute(sa.select(recruiter_opportunities)).mappings()
    for row in rows:
        if row["id"] in existing_opportunity_ids:
            continue
        lineage_id = str(uuid.uuid4())
        if row["source_email_id"] is not None:
            source_type = "gmail"
            external_id = str(row["source_email_id"])
            source_url = row["gmail_open_url"] or row["source_url"] or ""
        elif row["external_opportunity_id"] is not None:
            source_type = "nvoids"
            external_id = str(row["external_opportunity_id"])
            source_url = row["source_url"] or row["gmail_open_url"] or ""
        else:
            source_type = row["source_type"] or "gmail"
            external_id = ""
            source_url = row["source_url"] or row["gmail_open_url"] or ""
        now = datetime.now(UTC)
        bind.execute(
            lineages.insert().values(
                id=lineage_id,
                owner_id=row["owner_id"],
                origin_type=row["source_type"] or source_type,
                recruiter_opportunity_id=row["id"],
                current_status=(
                    "closed" if row["status"] in {"Closed", "Not Interested"} else "active"
                ),
                created_at=row["created_at"],
                closed_at=None,
            )
        )
        if external_id:
            bind.execute(
                source_references.insert().values(
                    owner_id=row["owner_id"],
                    lineage_id=lineage_id,
                    source_type=source_type,
                    external_id=external_id,
                    source_url=source_url,
                    first_seen_at=row["created_at"],
                    last_seen_at=row["created_at"],
                )
            )
        note = (
            "Current status inherited from RecruiterOpportunity at migration time; "
            "earlier history is unavailable."
        )
        if not external_id:
            note += " No source reference could be determined."
        bind.execute(
            lifecycle_events.insert().values(
                owner_id=row["owner_id"],
                lineage_id=lineage_id,
                event_type="lineage_backfilled",
                occurred_at=now,
                actor="system",
                process_name="migration",
                related_record_type="RecruiterOpportunity",
                related_record_id=row["id"],
                note=note,
                metadata_json="{}",
                created_at=now,
            )
        )


def _backfill_pending_review_cards() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(review_queue).where(
            review_queue.c.lineage_id.is_(None),
            review_queue.c.state == "pending",
        )
    ).mappings()
    for row in rows:
        lineage_id = str(uuid.uuid4())
        if row["source_email_id"] is not None:
            source_type = "gmail"
            external_id = str(row["source_email_id"])
        elif row["source_external_opportunity_id"] is not None:
            source_type = "nvoids"
            external_id = str(row["source_external_opportunity_id"])
        else:
            source_type = "gmail"
            external_id = ""
        now = datetime.now(UTC)
        bind.execute(
            lineages.insert().values(
                id=lineage_id,
                owner_id=row["owner_id"],
                origin_type=source_type,
                recruiter_opportunity_id=None,
                current_status="active",
                created_at=row["created_at"],
                closed_at=None,
            )
        )
        if external_id:
            bind.execute(
                source_references.insert().values(
                    owner_id=row["owner_id"],
                    lineage_id=lineage_id,
                    source_type=source_type,
                    external_id=external_id,
                    source_url=row["gmail_open_url"] or "",
                    first_seen_at=row["created_at"],
                    last_seen_at=row["created_at"],
                )
            )
        note = "Pending review card inherited at migration time; earlier history is unavailable."
        if not external_id:
            note += " No source reference could be determined."
        bind.execute(
            lifecycle_events.insert().values(
                owner_id=row["owner_id"],
                lineage_id=lineage_id,
                event_type="lineage_backfilled",
                occurred_at=now,
                actor="system",
                process_name="migration",
                related_record_type="NumberReviewQueue",
                related_record_id=row["id"],
                note=note,
                metadata_json="{}",
                created_at=now,
            )
        )
        bind.execute(
            review_queue.update()
            .where(review_queue.c.id == row["id"])
            .values(lineage_id=lineage_id)
        )


def upgrade() -> None:
    _create_lineage_tables()
    _add_review_lineage_column()
    _backfill_promoted_opportunities()
    _backfill_pending_review_cards()


def downgrade() -> None:
    bind = op.get_bind()
    if "lineage_id" in _columns("number_review_queue"):
        indexes = _indexes("number_review_queue")
        foreign_keys = _foreign_keys("number_review_queue")
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table("number_review_queue") as batch_op:
                if "ix_number_review_queue_lineage_id" in indexes:
                    batch_op.drop_index("ix_number_review_queue_lineage_id")
                if "fk_number_review_queue_lineage" in foreign_keys:
                    batch_op.drop_constraint(
                        "fk_number_review_queue_lineage",
                        type_="foreignkey",
                    )
                batch_op.drop_column("lineage_id")
        else:
            if "ix_number_review_queue_lineage_id" in indexes:
                op.drop_index("ix_number_review_queue_lineage_id", table_name="number_review_queue")
            if "fk_number_review_queue_lineage" in foreign_keys:
                op.drop_constraint(
                    "fk_number_review_queue_lineage",
                    "number_review_queue",
                    type_="foreignkey",
                )
            op.drop_column("number_review_queue", "lineage_id")

    tables = _table_names()
    for table_name in (
        "opportunity_lifecycle_events",
        "opportunity_source_references",
        "opportunity_lineages",
    ):
        if table_name in tables:
            op.drop_table(table_name)
