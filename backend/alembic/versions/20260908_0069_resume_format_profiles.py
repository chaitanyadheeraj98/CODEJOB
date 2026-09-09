"""Add per-employer resume format profiles.

`spec_json` is Text and deliberately unindexed - the only queries against this
table are by owner, and this migration runs on backend boot via
`alembic upgrade head`, where an index over an unbounded column blew the
Postgres btree key limit once already (see migration 0051).

Revision ID: 20260908_0069
Revises: 20260907_0068
"""

from alembic import op
import sqlalchemy as sa


revision = "20260908_0069"
down_revision = "20260907_0068"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("resume_format_profiles"):
        return

    op.create_table(
        "resume_format_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("source_file_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("spec_json", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resume_format_profiles_id", "resume_format_profiles", ["id"])
    op.create_index("ix_resume_format_profiles_owner_id", "resume_format_profiles", ["owner_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("resume_format_profiles"):
        op.drop_table("resume_format_profiles")
