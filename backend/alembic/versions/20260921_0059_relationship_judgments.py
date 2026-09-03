"""Add user judgments on inferred relationships.

Indexes cover owner_id, subject_type, subject_id, verdict and suppression_key -
all fixed-width. `correction_json` and `note` are Text and are deliberately
unindexed: this migration runs on backend boot via `alembic upgrade head`, and
an index over an unbounded column blew the Postgres btree key limit once
already (see migration 0051). `suppression_key` exists precisely so that
suppressing a rejected member set never requires querying correction_json.

Revision ID: 20260921_0059
Revises: 20260920_0058
"""

from alembic import op
import sqlalchemy as sa


revision = "20260921_0059"
down_revision = "20260920_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("relationship_judgments"):
        return

    op.create_table(
        "relationship_judgments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.String(length=64), nullable=False),
        sa.Column("verdict", sa.String(length=20), nullable=False),
        sa.Column("correction_json", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("suppression_key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_relationship_judgments_id", "relationship_judgments", ["id"])
    op.create_index("ix_relationship_judgments_owner_id", "relationship_judgments", ["owner_id"])
    op.create_index("ix_relationship_judgments_subject_type", "relationship_judgments", ["subject_type"])
    op.create_index("ix_relationship_judgments_subject_id", "relationship_judgments", ["subject_id"])
    op.create_index("ix_relationship_judgments_verdict", "relationship_judgments", ["verdict"])
    op.create_index("ix_relationship_judgments_suppression_key", "relationship_judgments", ["suppression_key"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("relationship_judgments"):
        op.drop_table("relationship_judgments")
