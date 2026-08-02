"""Formalize taxonomy tables and add continuous-learning fields.

Revision ID: 20260801_0012
Revises: 20260730_0011
Create Date: 2026-08-01 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260801_0012"
down_revision = "20260730_0011"
branch_labels = None
depends_on = None


def _columns(inspector, table: str) -> set[str]:
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "custom_skill_taxonomy_entries" not in tables:
        op.create_table(
            "custom_skill_taxonomy_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("canonical_name", sa.String(length=255), nullable=False),
            sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("category", sa.String(length=120), nullable=False, server_default="custom"),
            sa.Column("cluster_hint", sa.String(length=120), nullable=True),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("match_tier", sa.String(length=40), nullable=False, server_default="supporting"),
            sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("embedding_status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("embedding_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="approved"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )
    else:
        columns = _columns(inspector, "custom_skill_taxonomy_entries")
        additions = (
            ("description", sa.Column("description", sa.Text(), nullable=False, server_default="")),
            ("weight", sa.Column("weight", sa.Float(), nullable=False, server_default="1.0")),
            ("match_tier", sa.Column("match_tier", sa.String(length=40), nullable=False, server_default="supporting")),
            ("occurrence_count", sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0")),
            ("embedding_status", sa.Column("embedding_status", sa.String(length=40), nullable=False, server_default="pending")),
            ("embedding_json", sa.Column("embedding_json", sa.Text(), nullable=True)),
        )
        for name, column in additions:
            if name not in columns:
                op.add_column("custom_skill_taxonomy_entries", column)

    inspector = sa.inspect(bind)
    custom_indexes = {index["name"] for index in inspector.get_indexes("custom_skill_taxonomy_entries")}
    for name, columns in (
        ("ix_custom_skill_taxonomy_entries_owner_id", ["owner_id"]),
        ("ix_custom_skill_taxonomy_entries_canonical_name", ["canonical_name"]),
        ("ix_custom_skill_taxonomy_entries_status", ["status"]),
        ("ix_custom_skill_taxonomy_entries_embedding_status", ["embedding_status"]),
    ):
        if name not in custom_indexes:
            op.create_index(name, "custom_skill_taxonomy_entries", columns)

    tables = set(sa.inspect(bind).get_table_names())
    if "job_intent_taxonomy_entries" not in tables:
        op.create_table(
            "job_intent_taxonomy_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("phrase", sa.String(length=255), nullable=False),
            sa.Column("normalized_phrase", sa.String(length=255), nullable=False),
            sa.Column("polarity", sa.String(length=80), nullable=False),
            sa.Column("source_examples_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("sample_evidence_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("confidence_aggregate", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("last_intent_type", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if "canonical_entity_taxonomy_entries" not in tables:
        op.create_table(
            "canonical_entity_taxonomy_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("entity_type", sa.String(length=40), nullable=False),
            sa.Column("canonical_name", sa.String(length=255), nullable=False),
            sa.Column("aliases_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("embedding_status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("embedding_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="approved"),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint(
                "owner_id",
                "entity_type",
                "canonical_name",
                name="ux_canonical_entity_owner_type_name",
            ),
        )
        for column in ("owner_id", "entity_type", "canonical_name", "embedding_status", "status"):
            op.create_index(
                f"ix_canonical_entity_taxonomy_entries_{column}",
                "canonical_entity_taxonomy_entries",
                [column],
            )


def downgrade() -> None:
    op.drop_table("canonical_entity_taxonomy_entries")
    for name in ("embedding_json", "embedding_status", "occurrence_count", "match_tier", "weight", "description"):
        op.drop_column("custom_skill_taxonomy_entries", name)
