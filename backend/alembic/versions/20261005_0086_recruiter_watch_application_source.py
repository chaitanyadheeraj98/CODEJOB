"""Add application provenance to recruiter watches.

Revision ID: 20261005_0086
Revises: 20261004_0085
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261005_0086"
down_revision = "20261004_0085"
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    return column in {item["name"] for item in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "recruiter_watches", "source_application_ids_json"):
        op.add_column(
            "recruiter_watches",
            sa.Column("source_application_ids_json", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "recruiter_watches", "source_application_ids_json"):
        op.drop_column("recruiter_watches", "source_application_ids_json")
