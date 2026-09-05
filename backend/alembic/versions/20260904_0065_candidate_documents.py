"""Add the documents the user keeps on file to attach to a mail on request.

Separate from `attachment_assets` on purpose. That table is the automatic
pipeline's all-or-nothing set: every enabled row goes out with every draft it
approves. These are picked one mail at a time by name - "attach the passport and
the W2" - so the table carries no enabled flag, and nothing is ever sent unless
the user names the document and then confirms the send.

Indexes cover `id`, `owner_id` and the fixed-width `sha256` only. `file_path` is
Text and deliberately unindexed: this migration runs on backend boot via
`alembic upgrade head`, and an index over an unbounded column blew the Postgres
btree key limit once already (see migration 0051).

Revision ID: 20260904_0065
Revises: 20260904_0064
"""

from alembic import op
import sqlalchemy as sa


revision = "20260904_0065"
down_revision = "20260904_0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("candidate_documents"):
        return

    op.create_table(
        "candidate_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_id", sa.String(length=100), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column(
            "mime_type",
            sa.String(length=120),
            nullable=False,
            server_default="application/octet-stream",
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_candidate_documents_id", "candidate_documents", ["id"])
    op.create_index("ix_candidate_documents_owner_id", "candidate_documents", ["owner_id"])
    op.create_index("ix_candidate_documents_sha256", "candidate_documents", ["sha256"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("candidate_documents"):
        op.drop_table("candidate_documents")
