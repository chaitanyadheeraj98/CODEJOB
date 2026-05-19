"""External feed ingestion schema.

Revision ID: 20260519_0003
Revises: 20260518_0002
Create Date: 2026-05-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "20260519_0003"
down_revision = "20260518_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    table_names = set(inspector.get_table_names())

    if "recruiter_opportunities" in table_names:
        existing_cols = {row["name"] for row in inspector.get_columns("recruiter_opportunities")}
        if "source_type" not in existing_cols:
            op.add_column("recruiter_opportunities", sa.Column("source_type", sa.String(length=40), nullable=True))
        if "source_url" not in existing_cols:
            op.add_column("recruiter_opportunities", sa.Column("source_url", sa.String(length=1200), nullable=True))
        if "external_opportunity_id" not in existing_cols:
            op.add_column("recruiter_opportunities", sa.Column("external_opportunity_id", sa.Integer(), nullable=True))
        existing_indexes = {row["name"] for row in inspector.get_indexes("recruiter_opportunities")}
        if "ix_recruiter_opportunities_source_type" not in existing_indexes:
            op.create_index("ix_recruiter_opportunities_source_type", "recruiter_opportunities", ["source_type"], unique=False)
        if "ix_recruiter_opportunities_external_opportunity_id" not in existing_indexes:
            op.create_index(
                "ix_recruiter_opportunities_external_opportunity_id",
                "recruiter_opportunities",
                ["external_opportunity_id"],
                unique=False,
            )
        op.execute("UPDATE recruiter_opportunities SET source_type = 'gmail' WHERE source_type IS NULL")

    if "external_feed_sources" not in table_names:
        op.create_table(
            "external_feed_sources",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("base_url", sa.String(length=500), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False),
            sa.Column("poll_interval_minutes", sa.Integer(), nullable=False),
            sa.Column("last_sync_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {row["name"] for row in inspector.get_indexes("external_feed_sources")} if "external_feed_sources" in set(inspector.get_table_names()) else set()
    if "ix_external_feed_sources_id" not in existing_indexes:
        op.create_index("ix_external_feed_sources_id", "external_feed_sources", ["id"], unique=False)
    if "ix_external_feed_sources_owner_id" not in existing_indexes:
        op.create_index("ix_external_feed_sources_owner_id", "external_feed_sources", ["owner_id"], unique=False)
    if "ix_external_feed_sources_source_type" not in existing_indexes:
        op.create_index("ix_external_feed_sources_source_type", "external_feed_sources", ["source_type"], unique=False)

    table_names = set(inspector.get_table_names())
    if "external_opportunities" not in table_names:
        op.create_table(
            "external_opportunities",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("feed_source_id", sa.Integer(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("external_post_id", sa.String(length=255), nullable=False),
            sa.Column("source_url", sa.String(length=1200), nullable=False),
            sa.Column("posted_at", sa.DateTime(), nullable=True),
            sa.Column("recruiter_email", sa.String(length=255), nullable=False),
            sa.Column("recruiter_phone", sa.String(length=80), nullable=False),
            sa.Column("recruiter_name", sa.String(length=255), nullable=False),
            sa.Column("company", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=255), nullable=False),
            sa.Column("location", sa.String(length=255), nullable=False),
            sa.Column("work_mode", sa.String(length=80), nullable=False),
            sa.Column("visa_hints", sa.String(length=255), nullable=False),
            sa.Column("duration", sa.String(length=255), nullable=False),
            sa.Column("rate", sa.String(length=255), nullable=False),
            sa.Column("skills_text", sa.Text(), nullable=False),
            sa.Column("raw_body", sa.Text(), nullable=False),
            sa.Column("raw_html", sa.Text(), nullable=False),
            sa.Column("dedupe_hash", sa.String(length=80), nullable=False),
            sa.Column("parse_confidence", sa.Float(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(), nullable=False),
            sa.Column("bridge_status", sa.String(length=40), nullable=False),
            sa.Column("bridge_target_opportunity_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["feed_source_id"], ["external_feed_sources.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {row["name"] for row in inspector.get_indexes("external_opportunities")} if "external_opportunities" in set(inspector.get_table_names()) else set()
    if "ix_external_opportunities_id" not in existing_indexes:
        op.create_index("ix_external_opportunities_id", "external_opportunities", ["id"], unique=False)
    if "ix_external_opportunities_owner_id" not in existing_indexes:
        op.create_index("ix_external_opportunities_owner_id", "external_opportunities", ["owner_id"], unique=False)
    if "ix_external_opportunities_feed_source_id" not in existing_indexes:
        op.create_index("ix_external_opportunities_feed_source_id", "external_opportunities", ["feed_source_id"], unique=False)
    if "ix_external_opportunities_source_type" not in existing_indexes:
        op.create_index("ix_external_opportunities_source_type", "external_opportunities", ["source_type"], unique=False)
    if "ix_external_opportunities_external_post_id" not in existing_indexes:
        op.create_index("ix_external_opportunities_external_post_id", "external_opportunities", ["external_post_id"], unique=False)
    if "ix_external_opportunities_recruiter_email" not in existing_indexes:
        op.create_index("ix_external_opportunities_recruiter_email", "external_opportunities", ["recruiter_email"], unique=False)
    if "ix_external_opportunities_recruiter_phone" not in existing_indexes:
        op.create_index("ix_external_opportunities_recruiter_phone", "external_opportunities", ["recruiter_phone"], unique=False)
    if "ix_external_opportunities_dedupe_hash" not in existing_indexes:
        op.create_index("ix_external_opportunities_dedupe_hash", "external_opportunities", ["dedupe_hash"], unique=False)
    if "ix_external_opportunities_source_type_dedupe_hash" not in existing_indexes:
        op.create_index(
            "ix_external_opportunities_source_type_dedupe_hash",
            "external_opportunities",
            ["source_type", "dedupe_hash"],
            unique=False,
        )
    if "ix_external_opportunities_source_type_external_post_id" not in existing_indexes:
        op.create_index(
            "ix_external_opportunities_source_type_external_post_id",
            "external_opportunities",
            ["source_type", "external_post_id"],
            unique=False,
        )

    table_names = set(inspector.get_table_names())
    if "external_scrape_runs" not in table_names:
        op.create_table(
            "external_scrape_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.Column("fetched_count", sa.Integer(), nullable=False),
            sa.Column("created_count", sa.Integer(), nullable=False),
            sa.Column("deduped_count", sa.Integer(), nullable=False),
            sa.Column("failed_count", sa.Integer(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    existing_indexes = {row["name"] for row in inspector.get_indexes("external_scrape_runs")} if "external_scrape_runs" in set(inspector.get_table_names()) else set()
    if "ix_external_scrape_runs_id" not in existing_indexes:
        op.create_index("ix_external_scrape_runs_id", "external_scrape_runs", ["id"], unique=False)
    if "ix_external_scrape_runs_owner_id" not in existing_indexes:
        op.create_index("ix_external_scrape_runs_owner_id", "external_scrape_runs", ["owner_id"], unique=False)
    if "ix_external_scrape_runs_source_type" not in existing_indexes:
        op.create_index("ix_external_scrape_runs_source_type", "external_scrape_runs", ["source_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_external_scrape_runs_source_type", table_name="external_scrape_runs")
    op.drop_index("ix_external_scrape_runs_owner_id", table_name="external_scrape_runs")
    op.drop_index("ix_external_scrape_runs_id", table_name="external_scrape_runs")
    op.drop_table("external_scrape_runs")

    op.drop_index("ix_external_opportunities_source_type_external_post_id", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_source_type_dedupe_hash", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_dedupe_hash", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_recruiter_phone", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_recruiter_email", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_external_post_id", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_source_type", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_feed_source_id", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_owner_id", table_name="external_opportunities")
    op.drop_index("ix_external_opportunities_id", table_name="external_opportunities")
    op.drop_table("external_opportunities")

    op.drop_index("ix_external_feed_sources_source_type", table_name="external_feed_sources")
    op.drop_index("ix_external_feed_sources_owner_id", table_name="external_feed_sources")
    op.drop_index("ix_external_feed_sources_id", table_name="external_feed_sources")
    op.drop_table("external_feed_sources")

    op.drop_index("ix_recruiter_opportunities_external_opportunity_id", table_name="recruiter_opportunities")
    op.drop_index("ix_recruiter_opportunities_source_type", table_name="recruiter_opportunities")
    op.drop_column("recruiter_opportunities", "external_opportunity_id")
    op.drop_column("recruiter_opportunities", "source_url")
    op.drop_column("recruiter_opportunities", "source_type")
