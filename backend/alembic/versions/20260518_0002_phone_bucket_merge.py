"""Phone bucket dedupe/merge one-time migration.

Revision ID: 20260518_0002
Revises: 20260518_0001
Create Date: 2026-05-18
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect

from app.db import _merge_phone_buckets

# revision identifiers, used by Alembic.
revision = "20260518_0002"
down_revision = "20260518_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    inspector = inspect(bind)
    required = {"recruiter_numbers", "employer_numbers", "number_review_queue", "recruiter_opportunities"}
    existing = set(inspector.get_table_names())
    if required.issubset(existing):
        _merge_phone_buckets(bind)


def downgrade() -> None:
    # Irreversible data cleanup.
    pass
