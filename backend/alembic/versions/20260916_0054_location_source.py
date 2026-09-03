"""Provenance for location, completing the pair started in 0053.

Revision ID: 20260916_0054
Revises: 20260915_0053

A separate revision rather than an edit to 0053 because 0053 is already applied;
alembic never re-runs an applied revision, so amending it would leave the column
missing everywhere it had already run.

Adds one nullable column; touches no existing value. NULL keeps meaning
"unverified" and is never backfilled with an assumption about history.

Exists so a location filled from the approved taxonomy is distinguishable from one
the parser extracted. Writing a matched value with no marker would reintroduce the
silent-plausible-wrong-answer problem that role_source was added to remove - and
location is the field where a wrong guess is most likely, since a posting names
several cities for reasons unrelated to where the job is.

No index: not filtered on. Follows the repo's column-add convention
(20260908_0046) so it is idempotent against a create_all() schema.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260916_0054"
down_revision = "20260915_0053"
branch_labels = None
depends_on = None

COLUMN = "location_source"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if COLUMN in _columns("recruiter_emails"):
        return
    column = sa.Column(COLUMN, sa.String(length=40), nullable=True)
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            batch.add_column(column)
    else:
        op.add_column("recruiter_emails", column)


def downgrade() -> None:
    if COLUMN not in _columns("recruiter_emails"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("recruiter_emails") as batch:
            batch.drop_column(COLUMN)
    else:
        op.drop_column("recruiter_emails", COLUMN)
