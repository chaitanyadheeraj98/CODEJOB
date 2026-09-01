"""Repo-wide conventions for alembic revisions.

Two incidents motivated this file, both during the filter-picker work:

1. A revision added six btree indexes over unbounded ``Text`` columns. Postgres
   rejected them (``index row size 2896 exceeds btree version 4 maximum 2704``).
   Because ``docker-compose.yml`` runs ``alembic upgrade head`` inside the
   container ``command``, the backend exited and stayed down.
2. The same revision used a bare ``op.add_column``. Every migration test builds
   the schema with ``Base.metadata.create_all()`` - which always reflects the
   *current* models - then stamps an old revision and upgrades to ``head``. An
   unguarded add therefore fails with ``duplicate column name`` in every test
   whose stamp predates it, which broke ten unrelated test files at once.

Neither failure shows up in the revision's own test, so they are pinned here
instead. These are deliberately cheap source-level checks, not a substitute for
running the migration against Postgres.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

VERSIONS = Path(__file__).parents[1] / "alembic" / "versions"

# Columns that are unbounded Text in models.py. A composite btree over any of
# these can exceed Postgres's 2704-byte key limit on real data; measured maxima
# at the time of writing were 6632 / 458 / 305 characters respectively.
UNBOUNDED_TEXT_COLUMNS = {
    ("recruiter_emails", "role"),
    ("recruiter_opportunities", "job_title"),
    ("recruiter_opportunities", "end_client"),
    ("appts_applications", "job_title_snapshot"),
    ("appts_applications", "end_client_snapshot"),
    ("applications", "job_title_snapshot"),
    ("applications", "end_client_snapshot"),
}


def _revisions() -> list[Path]:
    return sorted(path for path in VERSIONS.glob("*.py") if path.name != "__init__.py")


class MigrationConventionTests(unittest.TestCase):
    def test_every_add_column_revision_guards_for_existing_columns(self) -> None:
        """A revision that adds columns must check whether they already exist.

        Heuristic by design: it asserts the revision consults the inspector at
        all, not that every individual column is guarded. That is enough to
        catch the real failure mode - a revision written with no guard whatever.
        """
        offenders = []
        for path in _revisions():
            source = path.read_text(encoding="utf-8")
            adds_columns = "op.add_column(" in source or "batch.add_column(" in source
            if not adds_columns:
                continue
            if "get_columns(" not in source and "has_column(" not in source:
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            "These revisions add columns without an existence guard. Follow the shape in "
            "20260908_0046_contact_secondary_company.py: build "
            "{c['name'] for c in sa.inspect(op.get_bind()).get_columns(table)} and skip "
            "columns already present. Without it, every migration test stamped before this "
            "revision fails with 'duplicate column name'.",
        )

    def test_every_create_table_revision_guards_for_existing_tables(self) -> None:
        """Same failure mode as the column guard, one level up.

        ``create_all()`` builds every current table, so an unguarded
        ``op.create_table`` raises ``table ... already exists`` in any migration
        test stamped before it.
        """
        offenders = []
        for path in _revisions():
            source = path.read_text(encoding="utf-8")
            if "op.create_table(" not in source:
                continue
            if "get_table_names()" not in source and "has_table" not in source:
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            "These revisions create tables without an existence guard. Check "
            "sa.inspect(op.get_bind()).get_table_names() and return early if the table is "
            "already present.",
        )

    def test_no_index_over_an_unbounded_text_column(self) -> None:
        """Guards the outage directly: no btree over a column with no length cap.

        SQLite has no btree key-length limit, so the SQLite-backed migration
        tests cannot catch this - it only surfaces against Postgres with real
        data, which in this project means "on container boot, in production".
        """
        offenders = []
        for path in _revisions():
            source = path.read_text(encoding="utf-8")
            for table, column in UNBOUNDED_TEXT_COLUMNS:
                # Matches both create_index("name", "table", ["a", "b", column])
                # and the ("name", "table", [...]) tuple-list style used by 0038.
                for match in re.finditer(rf'"{re.escape(table)}"\s*,\s*\[([^\]]*)\]', source):
                    listed = {item.strip().strip("\"'") for item in match.group(1).split(",")}
                    if column in listed and "create_index" in source:
                        offenders.append(f"{path.name}: {table}.{column}")
        self.assertEqual(
            sorted(set(offenders)),
            [],
            "These revisions index an unbounded Text column. Postgres rejects the index once "
            "any row exceeds ~2704 bytes of key, and since migrations run in the container "
            "command that takes the backend down on boot. Narrowing the column is not an "
            "escape hatch either - real rows exceed 255 chars. See "
            "20260913_0051_visible_filters.py for the full rationale.",
        )
