"""Per-dashboard filter visibility preference.

Revision ID: 20260913_0051
Revises: 20260912_0050

Deliberately ships no indexes.

An earlier draft of this revision also created covering btree indexes for the
GET /filter-options DISTINCT lookups, including (owner_id, state, role) on
recruiter_emails. That aborted on real data: RecruiterEmail.role,
RecruiterOpportunity.job_title and RecruiterOpportunity.end_client are unbounded
Text (models.py:35, :775, :776), and one existing row produced a 2896-byte index
tuple against Postgres's 2704-byte btree key limit. Because the container runs
`alembic upgrade head` on boot, that took the whole backend down.

Those indexes were speculative - no EXPLAIN ever showed /filter-options to be
slow, and the endpoint is already LIMIT-bounded behind a 300ms client debounce.
Measured on live data after the incident:

    recruiter_emails.role                8462 rows  max 6632  avg 49  (47 > 255)
    recruiter_opportunities.job_title    1055 rows  max  458  avg 42  ( 1 > 255)
    recruiter_opportunities.end_client   1055 rows  max  305  avg  2  ( 1 > 255)

At those row counts a seq scan + sort under LIMIT 20 costs single-digit
milliseconds, so there is nothing here worth indexing. Do not re-add them
without an EXPLAIN ANALYZE showing a real problem.

Note also that narrowing these columns to varchar(255) so they *could* be
indexed is not an option: 47 role values exceed 255 characters, and role's
6632-character maximum shows the extractor sometimes stores a whole subject
line rather than a job title. Narrowing would silently truncate real rows.
left()/partial indexes are equally unusable here - they only get used when the
query repeats the same expression, which would change the values the picker
returns.

Note that the SQLite-backed migration test cannot catch that class of failure:
SQLite has no btree key-length limit, so an index Postgres rejects passes there.
Any future revision that adds indexes needs to be exercised against Postgres.
"""
from alembic import op
import sqlalchemy as sa

revision = "20260913_0051"
down_revision = "20260912_0050"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "visible_filters_json" in _columns("user_settings"):
        return
    column = sa.Column("visible_filters_json", sa.Text(), nullable=False, server_default="{}")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("user_settings") as batch:
            batch.add_column(column)
    else:
        op.add_column("user_settings", column)


def downgrade() -> None:
    if "visible_filters_json" not in _columns("user_settings"):
        return
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("user_settings") as batch:
            batch.drop_column("visible_filters_json")
    else:
        op.drop_column("user_settings", "visible_filters_json")
