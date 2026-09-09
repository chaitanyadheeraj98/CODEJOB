"""Add resume drafts.

A separate revision rather than an addition to 0069 because 0069 had already run
in an environment: alembic never re-applies a revision it has stamped, so a table
added to an applied migration would exist nowhere and fail everywhere.

`source_resume_id` is a plain indexed integer rather than a foreign key - a draft
is the user's own work and has to survive the variant it was copied from being
deleted. `content_markdown` is Text and deliberately unindexed: this migration
runs on backend boot via `alembic upgrade head`, where an index over an unbounded
column blew the Postgres btree key limit once already (see migration 0051).

Revision ID: 20260909_0070
Revises: 20260908_0069
"""

from alembic import op
import sqlalchemy as sa


revision = "20260909_0070"
down_revision = "20260908_0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("resume_drafts"):
        return

    op.create_table(
        "resume_drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("source_resume_id", sa.Integer(), nullable=True),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resume_drafts_id", "resume_drafts", ["id"])
    op.create_index("ix_resume_drafts_owner_id", "resume_drafts", ["owner_id"])
    op.create_index("ix_resume_drafts_source_resume_id", "resume_drafts", ["source_resume_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("resume_drafts"):
        op.drop_table("resume_drafts")
