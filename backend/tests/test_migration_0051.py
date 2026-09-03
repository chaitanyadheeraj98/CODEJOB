"""Migration 0051 adds user_settings.visible_filters_json and nothing else.

Scope note: this test runs against a temporary SQLite file, following the
pattern in test_migration_0050.py. That is sufficient for a column add, but it
is NOT sufficient for index DDL - SQLite has no btree key-length limit, so an
index Postgres rejects (as happened with the withdrawn
(owner_id, state, role) index over the unbounded Text column
RecruiterEmail.role) passes here silently. If a future revision adds indexes,
it needs a Postgres-backed test; do not assume this file covers them.
"""

import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    return config


class VisibleFiltersMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            settings.database_url = database_url
            engine = sa.create_engine(database_url)
            try:
                config = _alembic_config()
                command.upgrade(config, "20260912_0050")
                command.upgrade(config, "head")

                columns = {
                    column["name"]: column
                    for column in sa.inspect(engine).get_columns("user_settings")
                }
                self.assertIn("visible_filters_json", columns)
                self.assertEqual(
                    str(columns["visible_filters_json"]["default"]).strip("'\"()"),
                    "{}",
                )

                command.downgrade(config, "20260912_0050")
                self.assertNotIn(
                    "visible_filters_json",
                    {column["name"] for column in sa.inspect(engine).get_columns("user_settings")},
                )
            finally:
                engine.dispose()
                settings.database_url = previous_url

    def test_revision_creates_no_indexes(self) -> None:
        """Guards the regression directly: 0051 must not add index DDL.

        The withdrawn indexes crashed the backend on boot because
        `alembic upgrade head` runs in the container entrypoint. Re-adding them
        here - where the SQLite test would happily pass - is the exact mistake
        this asserts against.
        """
        source = (
            Path(__file__).parents[1]
            / "alembic"
            / "versions"
            / "20260913_0051_visible_filters.py"
        ).read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]
        self.assertNotIn("create_index", body)
        self.assertNotIn("drop_index", body)
