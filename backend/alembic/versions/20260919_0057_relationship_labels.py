"""Add the relationship labeling set.

Indexes cover owner_id, the two FK columns, verdict, split and sampler - all
fixed-width or foreign keys. `reason` is Text and is deliberately unindexed:
this migration runs on backend boot via `alembic upgrade head`, and an index
over an unbounded column blew the Postgres btree key limit once already (see
migration 0051).

Revision ID: 20260919_0057
Revises: 20260918_0056
"""

from alembic import op
import sqlalchemy as sa


revision = "20260919_0057"
down_revision = "20260918_0056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("relationship_labels"):
        return

    op.create_table(
        "relationship_labels",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("left_opportunity_id", sa.Integer(), nullable=False),
        sa.Column("right_opportunity_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("split", sa.String(length=10), nullable=False),
        sa.Column("labeler", sa.String(length=100), nullable=False),
        sa.Column("sampler", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["left_opportunity_id"],
            ["recruiter_opportunities.id"],
            name="fk_relationship_label_left",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["right_opportunity_id"],
            ["recruiter_opportunities.id"],
            name="fk_relationship_label_right",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Canonical ordering (left < right) is enforced at the service
        # boundary; without it this constraint permits both orderings and one
        # pair can be labeled twice with opposite verdicts.
        sa.UniqueConstraint(
            "owner_id",
            "left_opportunity_id",
            "right_opportunity_id",
            name="ux_relationship_label_owner_pair",
        ),
    )
    op.create_index("ix_relationship_labels_id", "relationship_labels", ["id"])
    op.create_index("ix_relationship_labels_owner_id", "relationship_labels", ["owner_id"])
    op.create_index("ix_relationship_labels_left_opportunity_id", "relationship_labels", ["left_opportunity_id"])
    op.create_index("ix_relationship_labels_right_opportunity_id", "relationship_labels", ["right_opportunity_id"])
    op.create_index("ix_relationship_labels_verdict", "relationship_labels", ["verdict"])
    op.create_index("ix_relationship_labels_split", "relationship_labels", ["split"])
    op.create_index("ix_relationship_labels_sampler", "relationship_labels", ["sampler"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("relationship_labels"):
        op.drop_table("relationship_labels")
