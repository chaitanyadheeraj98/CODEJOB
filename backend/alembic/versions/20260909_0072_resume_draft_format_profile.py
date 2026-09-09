"""Remember a draft's format profile.

Revision ID: 20260909_0072
Revises: 20260908_0071
"""

from alembic import op
import sqlalchemy as sa

revision = "20260909_0072"
down_revision = "20260908_0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "format_profile_id" not in {c["name"] for c in inspector.get_columns("resume_drafts")}:
        op.add_column("resume_drafts", sa.Column("format_profile_id", sa.Integer(), nullable=True))
    if "ix_resume_drafts_format_profile_id" not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("resume_drafts")}:
        op.create_index("ix_resume_drafts_format_profile_id", "resume_drafts", ["format_profile_id"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ix_resume_drafts_format_profile_id" in {i["name"] for i in inspector.get_indexes("resume_drafts")}:
        op.drop_index("ix_resume_drafts_format_profile_id", table_name="resume_drafts")
    if "format_profile_id" in {c["name"] for c in inspector.get_columns("resume_drafts")}:
        op.drop_column("resume_drafts", "format_profile_id")
