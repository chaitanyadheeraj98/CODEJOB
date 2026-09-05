"""Give the preserved legacy columns the server defaults 0018 intended.

`20260817_0018_declarative_baseline` preserves a set of retired resume-matching
columns outside the ORM, and says why the defaults matter:

    # Preserved live-only legacy column. The default keeps ORM inserts valid.
    _c("feature_resume_matching_enabled", "boolean", server_default="false")

But 0018 only *creates tables missing from historical migrations*. A database
that already had these columns - the original live SQLite volume, and anything
descended from it - kept them exactly as they were: NOT NULL with no default.
The ORM does not know the columns exist, so every INSERT it builds omits them,
and the database rejects it:

    NOT NULL constraint failed: user_settings.feature_resume_matching_enabled

That is not a settings-only fault. `recruiter_emails` and `resume_assets` carry
the same shape, so on an affected database the backend cannot store a recruiter
email, save a resume, or create a settings row - and since
`ensure_default_settings()` runs in `startup_service.startup()`, it fails to
boot at all.

This revision finishes 0018's job on the databases 0018 could not reach. It
preserves the columns, as 0018 decided; it only makes them insertable.

`recruiter_emails`' three columns are the ones 0018 never covered, because that
table already existed and so was never rebuilt. Their defaults follow the naming
the rest of the set uses: a `_summary_`/`_profile_` object defaults to `'{}'`,
a plural list to `'[]'`. Nothing reads these values - no ORM model, no service,
and `app/resume_matching/` holds nothing but a stale `__pycache__` - so the only
requirement is that they are valid, non-NULL, and never block a write again.

**Every change is skipped when the column is absent or already has a default.**
A database built by `create_all()` has no legacy columns and is untouched; one
built by running migrations got its defaults from 0018 and is untouched. In
practice only a volume descended from the original SQLite file does any work
here, which matters because `alembic upgrade head` runs in the docker-compose
container command: on Postgres each change is a metadata-only SET DEFAULT, and
the SQLite table rebuild batch mode performs only ever runs on a database that
is currently unable to accept writes at all.

Revision ID: 20260904_0064
Revises: 20260904_0063
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260904_0064"
down_revision = "20260904_0063"
branch_labels = None
depends_on = None


# (table, column, existing type, the default 0018 specifies - or its convention).
# Only NOT NULL legacy columns appear here. The nullable ones in the preserved
# set (display_name, skills_embedding, jd_profile_json, ...) already accept an
# omitted INSERT and need nothing.
_LEGACY_DEFAULTS: tuple[tuple[str, str, sa.types.TypeEngine, sa.ClauseElement], ...] = (
    ("user_settings", "feature_resume_matching_enabled", sa.Boolean(), sa.false()),
    ("resume_assets", "normalized_skills_json", sa.Text(), sa.text("'[]'")),
    ("resume_assets", "skills_extraction_status", sa.String(40), sa.text("'pending'")),
    ("resume_assets", "evidence_profile_json", sa.Text(), sa.text("'{}'")),
    ("resume_assets", "evidence_extraction_status", sa.String(40), sa.text("'pending'")),
    ("resume_assets", "profile_version", sa.String(40), sa.text("'resume_match_v1'")),
    ("recruiter_emails", "evidence_summary_json", sa.Text(), sa.text("'{}'")),
    ("recruiter_emails", "missing_requirements_json", sa.Text(), sa.text("'[]'")),
    ("recruiter_emails", "risk_flags_json", sa.Text(), sa.text("'[]'")),
)


def _columns_without_defaults() -> dict[str, list[tuple[str, sa.types.TypeEngine, sa.ClauseElement]]]:
    """Group the work by table, skipping everything already correct.

    Batch mode rebuilds a table on SQLite, so a table with nothing to fix must
    not appear here at all.
    """
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    pending: dict[str, list[tuple[str, sa.types.TypeEngine, sa.ClauseElement]]] = {}
    for table, column, column_type, default in _LEGACY_DEFAULTS:
        if table not in tables:
            continue
        existing = {row["name"]: row for row in inspector.get_columns(table)}
        row = existing.get(column)
        # Absent: a create_all() database, which never had the legacy set.
        # Already defaulted: 0018 built this table, so there is nothing to do.
        if row is None or row.get("default") is not None:
            continue
        pending.setdefault(table, []).append((column, column_type, default))
    return pending


def upgrade() -> None:
    for table, changes in _columns_without_defaults().items():
        with op.batch_alter_table(table) as batch:
            for column, column_type, default in changes:
                batch.alter_column(
                    column,
                    existing_type=column_type,
                    existing_nullable=False,
                    server_default=default,
                )


def downgrade() -> None:
    """Deliberately a no-op.

    Removing these defaults would restore a schema that cannot accept an INSERT
    from the current ORM - it would re-break the boot this revision fixes. The
    columns themselves are untouched either way, so there is nothing here that a
    downgrade needs to undo.
    """
