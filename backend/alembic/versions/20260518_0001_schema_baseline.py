"""Schema baseline for migration-owned startup.

Revision ID: 20260518_0001
Revises:
Create Date: 2026-05-18
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260518_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Transitional baseline: existing deployments may already have schema created
    # by legacy startup patching. This revision establishes Alembic ownership.
    pass


def downgrade() -> None:
    pass
