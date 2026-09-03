"""Let deleting a premium_number_lead version null out the review that pointed
to it instead of being blocked - the review keeps its own snapshot fields
(company, owner_name, reason_code, evidence), it just loses the lead pointer.

Revision ID: 20260905_0043
Revises: 20260904_0042
"""
from alembic import op
import sqlalchemy as sa

revision = "20260905_0043"
down_revision = "20260904_0042"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "fk_number_review_queue_source_lead"


def _existing_fk() -> dict | None:
    return next(
        (fk for fk in sa.inspect(op.get_bind()).get_foreign_keys("number_review_queue") if fk.get("name") == CONSTRAINT_NAME),
        None,
    )


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_fk()
    if existing and (existing.get("options") or {}).get("ondelete") == "SET NULL":
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            if existing:
                batch.drop_constraint(CONSTRAINT_NAME, type_="foreignkey")
            batch.create_foreign_key(CONSTRAINT_NAME, "premium_number_leads", ["source_lead_id"], ["id"], ondelete="SET NULL")
    else:
        if existing:
            op.drop_constraint(CONSTRAINT_NAME, "number_review_queue", type_="foreignkey")
        op.create_foreign_key(CONSTRAINT_NAME, "number_review_queue", "premium_number_leads", ["source_lead_id"], ["id"], ondelete="SET NULL")


def downgrade() -> None:
    bind = op.get_bind()
    existing = _existing_fk()
    if not existing or (existing.get("options") or {}).get("ondelete") != "SET NULL":
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("number_review_queue") as batch:
            batch.drop_constraint(CONSTRAINT_NAME, type_="foreignkey")
            batch.create_foreign_key(CONSTRAINT_NAME, "premium_number_leads", ["source_lead_id"], ["id"])
    else:
        op.drop_constraint(CONSTRAINT_NAME, "number_review_queue", type_="foreignkey")
        op.create_foreign_key(CONSTRAINT_NAME, "number_review_queue", "premium_number_leads", ["source_lead_id"], ["id"])
