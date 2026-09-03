"""Add permanent, user-facing candidate Record ID, independent of OpportunityLineage.

Revision ID: 20260826_0032
Revises: 20260825_0031
Create Date: 2026-08-26
"""

from __future__ import annotations

import uuid

from alembic import op
import sqlalchemy as sa


revision = "20260826_0032"
down_revision = "20260825_0031"
branch_labels = None
depends_on = None


candidate_records = sa.table(
    "candidate_records",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("origin_type"),
    sa.column("internal_lineage_id"),
    sa.column("created_at"),
)
recruiter_emails = sa.table(
    "recruiter_emails",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("source"),
    sa.column("external_message_id"),
    sa.column("gmail_received_at"),
    sa.column("created_at"),
    sa.column("record_id"),
)
external_opportunities = sa.table(
    "external_opportunities",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("external_post_id"),
    sa.column("posted_at"),
    sa.column("created_at"),
    sa.column("record_id"),
)
number_review_queue = sa.table(
    "number_review_queue",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("lineage_id"),
    sa.column("source_email_id"),
    sa.column("source_external_opportunity_id"),
    sa.column("created_at"),
    sa.column("record_id"),
)
recruiter_opportunities = sa.table(
    "recruiter_opportunities",
    sa.column("id"),
    sa.column("owner_id"),
    sa.column("source_type"),
    sa.column("source_email_id"),
    sa.column("external_opportunity_id"),
    sa.column("created_at"),
    sa.column("record_id"),
)
opportunity_lineages = sa.table(
    "opportunity_lineages",
    sa.column("id"),
    sa.column("recruiter_opportunity_id"),
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


def _create_candidate_records_table() -> None:
    if "candidate_records" in _table_names():
        return
    op.create_table(
        "candidate_records",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("origin_type", sa.String(length=20), nullable=False),
        sa.Column("internal_lineage_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["internal_lineage_id"],
            ["opportunity_lineages.id"],
            name="fk_candidate_record_lineage",
        ),
    )
    for name, columns, unique in (
        ("ix_candidate_records_owner_id", ["owner_id"], False),
        ("ix_candidate_records_origin_type", ["origin_type"], False),
        ("ix_candidate_records_internal_lineage_id", ["internal_lineage_id"], True),
    ):
        _create_index(name, "candidate_records", columns, unique=unique)


def _add_record_id_column(table_name: str, fk_name: str, index_name: str) -> None:
    bind = op.get_bind()
    column_missing = "record_id" not in _columns(table_name)
    foreign_key_missing = fk_name not in _foreign_keys(table_name)
    if bind.dialect.name == "sqlite" and (column_missing or foreign_key_missing):
        with op.batch_alter_table(table_name) as batch_op:
            if column_missing:
                batch_op.add_column(sa.Column("record_id", sa.String(length=36), nullable=True))
            if foreign_key_missing:
                batch_op.create_foreign_key(fk_name, "candidate_records", ["record_id"], ["id"])
    else:
        if column_missing:
            op.add_column(table_name, sa.Column("record_id", sa.String(length=36), nullable=True))
        if foreign_key_missing:
            op.create_foreign_key(fk_name, table_name, "candidate_records", ["record_id"], ["id"])
    _create_index(index_name, table_name, ["record_id"])


def _add_record_id_columns() -> None:
    _add_record_id_column("recruiter_emails", "fk_recruiter_emails_record", "ix_recruiter_emails_record_id")
    _add_record_id_column(
        "external_opportunities", "fk_external_opportunities_record", "ix_external_opportunities_record_id"
    )
    _add_record_id_column(
        "number_review_queue", "fk_number_review_queue_record", "ix_number_review_queue_record_id"
    )
    _add_record_id_column(
        "recruiter_opportunities", "fk_recruiter_opportunities_record", "ix_recruiter_opportunities_record_id"
    )


def _insert_record(*, owner_id: str, origin_type: str, created_at) -> str:
    bind = op.get_bind()
    record_id = str(uuid.uuid4())
    bind.execute(
        candidate_records.insert().values(
            id=record_id,
            owner_id=owner_id,
            origin_type=origin_type,
            internal_lineage_id=None,
            created_at=created_at,
        )
    )
    return record_id


def _backfill_external_opportunities() -> dict[str, str]:
    """Pass 1: every ExternalOpportunity row gets its own fresh Record - mirrors the live
    anchor point B, which mints one unconditionally right after the row is created, before
    bridging/enqueue decisions are made. Returns external_post_id -> record_id for pass 2's
    nvoids RecruiterEmail matching."""
    bind = op.get_bind()
    post_id_to_record_id: dict[str, str] = {}
    rows = bind.execute(
        sa.select(external_opportunities).where(external_opportunities.c.record_id.is_(None))
    ).mappings()
    for row in rows:
        record_id = _insert_record(
            owner_id=row["owner_id"],
            origin_type="nvoids",
            created_at=row["posted_at"] or row["created_at"],
        )
        bind.execute(
            external_opportunities.update()
            .where(external_opportunities.c.id == row["id"])
            .values(record_id=record_id)
        )
        if row["external_post_id"]:
            post_id_to_record_id[row["external_post_id"]] = record_id
    return post_id_to_record_id


def _backfill_recruiter_emails(post_id_to_record_id: dict[str, str]) -> None:
    """Pass 2: every RecruiterEmail row gets a Record - reusing the matching
    ExternalOpportunity's Record for a nvoids-bridged email (f"nvoids:{external_post_id}",
    same convention external_feeds/service.py already uses - a multi-role child's
    ":req:"-suffixed id deliberately never matches this, so it correctly falls through to
    its own fresh Record, mirroring A8's "each multi-role child gets its own Record")."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(recruiter_emails).where(recruiter_emails.c.record_id.is_(None))
    ).mappings()
    for row in rows:
        record_id = None
        external_message_id = row["external_message_id"] or ""
        if row["source"] == "nvoids" and external_message_id.startswith("nvoids:"):
            post_id = external_message_id[len("nvoids:") :]
            record_id = post_id_to_record_id.get(post_id)
        if record_id is None:
            record_id = _insert_record(
                owner_id=row["owner_id"],
                origin_type="nvoids" if row["source"] == "nvoids" else "gmail",
                created_at=row["gmail_received_at"] or row["created_at"],
            )
        bind.execute(
            recruiter_emails.update().where(recruiter_emails.c.id == row["id"]).values(record_id=record_id)
        )


def _backfill_recruiter_opportunities() -> None:
    """Pass 3: every RecruiterOpportunity resolves (never mints fresh) its Record from its
    already-backfilled source row - mirrors resolve_record_id's own resolution order. Links
    the Record's internal_lineage_id when this opportunity has a lineage, exactly matching
    what attach_recruiter_opportunity + link_record_to_lineage do together on the live path."""
    bind = op.get_bind()
    lineage_by_opportunity = {
        row["recruiter_opportunity_id"]: row["id"]
        for row in bind.execute(
            sa.select(opportunity_lineages).where(opportunity_lineages.c.recruiter_opportunity_id.is_not(None))
        ).mappings()
    }
    rows = bind.execute(
        sa.select(recruiter_opportunities).where(recruiter_opportunities.c.record_id.is_(None))
    ).mappings()
    for row in rows:
        record_id = None
        if row["source_email_id"] is not None:
            source_row = bind.execute(
                sa.select(recruiter_emails.c.record_id).where(recruiter_emails.c.id == row["source_email_id"])
            ).scalar()
            record_id = source_row
        elif row["external_opportunity_id"] is not None:
            source_row = bind.execute(
                sa.select(external_opportunities.c.record_id).where(
                    external_opportunities.c.id == row["external_opportunity_id"]
                )
            ).scalar()
            record_id = source_row
        if record_id is None:
            record_id = _insert_record(
                owner_id=row["owner_id"],
                origin_type="nvoids" if row["source_type"] == "nvoids" else "gmail",
                created_at=row["created_at"],
            )
        bind.execute(
            recruiter_opportunities.update()
            .where(recruiter_opportunities.c.id == row["id"])
            .values(record_id=record_id)
        )
        lineage_id = lineage_by_opportunity.get(row["id"])
        if lineage_id is not None:
            bind.execute(
                candidate_records.update()
                .where(candidate_records.c.id == record_id)
                .values(internal_lineage_id=lineage_id)
            )


def _backfill_number_review_queue() -> None:
    """Pass 4: every NumberReviewQueue row resolves its Record from its already-backfilled
    source row, same resolution order as pass 3. Links internal_lineage_id when the card
    already has a lineage_id (set at review-queue creation time, before promotion) -
    idempotent with pass 3's own link when the card was later promoted, since both resolve
    to the same underlying source row's Record."""
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(number_review_queue).where(number_review_queue.c.record_id.is_(None))
    ).mappings()
    for row in rows:
        record_id = None
        if row["source_email_id"] is not None:
            record_id = bind.execute(
                sa.select(recruiter_emails.c.record_id).where(recruiter_emails.c.id == row["source_email_id"])
            ).scalar()
        elif row["source_external_opportunity_id"] is not None:
            record_id = bind.execute(
                sa.select(external_opportunities.c.record_id).where(
                    external_opportunities.c.id == row["source_external_opportunity_id"]
                )
            ).scalar()
        if record_id is None:
            record_id = _insert_record(owner_id=row["owner_id"], origin_type="gmail", created_at=row["created_at"])
        bind.execute(
            number_review_queue.update().where(number_review_queue.c.id == row["id"]).values(record_id=record_id)
        )
        if row["lineage_id"] is not None:
            bind.execute(
                candidate_records.update()
                .where(candidate_records.c.id == record_id)
                .values(internal_lineage_id=row["lineage_id"])
            )


def upgrade() -> None:
    _create_candidate_records_table()
    _add_record_id_columns()
    post_id_to_record_id = _backfill_external_opportunities()
    _backfill_recruiter_emails(post_id_to_record_id)
    _backfill_recruiter_opportunities()
    _backfill_number_review_queue()


def downgrade() -> None:
    bind = op.get_bind()
    for table_name, fk_name, index_name in (
        ("recruiter_opportunities", "fk_recruiter_opportunities_record", "ix_recruiter_opportunities_record_id"),
        ("number_review_queue", "fk_number_review_queue_record", "ix_number_review_queue_record_id"),
        ("external_opportunities", "fk_external_opportunities_record", "ix_external_opportunities_record_id"),
        ("recruiter_emails", "fk_recruiter_emails_record", "ix_recruiter_emails_record_id"),
    ):
        if "record_id" not in _columns(table_name):
            continue
        indexes = _indexes(table_name)
        foreign_keys = _foreign_keys(table_name)
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table_name) as batch_op:
                if index_name in indexes:
                    batch_op.drop_index(index_name)
                if fk_name in foreign_keys:
                    batch_op.drop_constraint(fk_name, type_="foreignkey")
                batch_op.drop_column("record_id")
        else:
            if index_name in indexes:
                op.drop_index(index_name, table_name=table_name)
            if fk_name in foreign_keys:
                op.drop_constraint(fk_name, table_name, type_="foreignkey")
            op.drop_column(table_name, "record_id")

    if "candidate_records" in _table_names():
        op.drop_table("candidate_records")
