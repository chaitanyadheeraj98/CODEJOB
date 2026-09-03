"""Add inferred opportunity clusters and their members.

Indexes cover owner_id, confidence, status, semantic_available, member_key and
the two FK columns - all fixed-width or foreign keys. `evidence_json` is Text
and is deliberately unindexed: this migration runs on backend boot via
`alembic upgrade head`, and an index over an unbounded column blew the Postgres
btree key limit once already (see migration 0051).

`status` defaults to "shadow". A cluster is persisted long before it may be
shown, and the default is what a forgotten code path gets.

Revision ID: 20260920_0058
Revises: 20260919_0057
"""

from alembic import op
import sqlalchemy as sa


revision = "20260920_0058"
down_revision = "20260919_0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("opportunity_clusters"):
        op.create_table(
            "opportunity_clusters",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("label", sa.String(length=255), nullable=False),
            sa.Column("inferred_end_client", sa.String(length=255), nullable=False),
            sa.Column("inferred_partner", sa.String(length=255), nullable=False),
            sa.Column("inferred_domain", sa.String(length=255), nullable=False),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=20), nullable=False),
            sa.Column("method", sa.String(length=40), nullable=False),
            sa.Column("semantic_available", sa.Boolean(), nullable=False),
            sa.Column("member_key", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_opportunity_clusters_owner_id", "opportunity_clusters", ["owner_id"])
        op.create_index("ix_opportunity_clusters_confidence", "opportunity_clusters", ["confidence"])
        op.create_index("ix_opportunity_clusters_status", "opportunity_clusters", ["status"])
        op.create_index("ix_opportunity_clusters_semantic_available", "opportunity_clusters", ["semantic_available"])
        op.create_index("ix_opportunity_clusters_member_key", "opportunity_clusters", ["member_key"])

    if not inspector.has_table("opportunity_cluster_members"):
        op.create_table(
            "opportunity_cluster_members",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("owner_id", sa.String(length=100), nullable=False),
            sa.Column("cluster_id", sa.String(length=36), nullable=False),
            sa.Column("opportunity_id", sa.Integer(), nullable=False),
            sa.Column("confidence", sa.String(length=20), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["cluster_id"],
                ["opportunity_clusters.id"],
                name="fk_cluster_member_cluster",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["opportunity_id"],
                ["recruiter_opportunities.id"],
                name="fk_cluster_member_opportunity",
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            # A re-run must not double-add a member.
            sa.UniqueConstraint("cluster_id", "opportunity_id", name="ux_cluster_member"),
        )
        op.create_index("ix_opportunity_cluster_members_id", "opportunity_cluster_members", ["id"])
        op.create_index("ix_opportunity_cluster_members_owner_id", "opportunity_cluster_members", ["owner_id"])
        op.create_index("ix_opportunity_cluster_members_cluster_id", "opportunity_cluster_members", ["cluster_id"])
        op.create_index("ix_opportunity_cluster_members_opportunity_id", "opportunity_cluster_members", ["opportunity_id"])
        op.create_index("ix_opportunity_cluster_members_confidence", "opportunity_cluster_members", ["confidence"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("opportunity_cluster_members"):
        op.drop_table("opportunity_cluster_members")
    if inspector.has_table("opportunity_clusters"):
        op.drop_table("opportunity_clusters")
