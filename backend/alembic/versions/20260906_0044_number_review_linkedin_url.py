"""Add linkedin_url to number_review_queue so pending-review contacts can
have a LinkedIn profile captured before they're promoted to recruiter/employer.

Revision ID: 20260906_0044
Revises: 20260905_0043
"""
from alembic import op
import sqlalchemy as sa

revision = "20260906_0044"
down_revision = "20260905_0043"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "linkedin_url" in _columns("number_review_queue"):
        return
    column = sa.Column("linkedin_url", sa.String(500), nullable=False, server_default="")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            batch.add_column(column)
    else:
        op.add_column("number_review_queue", column)


def downgrade() -> None:
    if "linkedin_url" not in _columns("number_review_queue"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            batch.drop_column("linkedin_url")
    else:
        op.drop_column("number_review_queue", "linkedin_url")
