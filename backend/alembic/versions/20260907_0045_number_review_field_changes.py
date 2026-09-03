"""Add field_changes_json to number_review_queue so a review card produced by
automatic contact enrichment can show the reviewer old-vs-new values, not just
a toast the change happened.

Revision ID: 20260907_0045
Revises: 20260906_0044
"""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0045"
down_revision = "20260906_0044"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "field_changes_json" in _columns("number_review_queue"):
        return
    column = sa.Column("field_changes_json", sa.Text(), nullable=False, server_default="")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            batch.add_column(column)
    else:
        op.add_column("number_review_queue", column)


def downgrade() -> None:
    if "field_changes_json" not in _columns("number_review_queue"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            batch.drop_column("field_changes_json")
    else:
        op.drop_column("number_review_queue", "field_changes_json")
