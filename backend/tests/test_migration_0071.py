"""Migration 0071: the gmail/nvoids content fingerprint.

`alembic upgrade head` runs on backend boot, so the shape asserted here is the
shape that reaches production unattended: a nullable, fixed-width column and an
index that is safe to build (migration 0051 blew the Postgres btree key limit
with an index over an unbounded column).
"""

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import app.models  # noqa: F401  - populates Base.metadata
from app.config import settings
from app.db import Base

REVISION = "20260908_0071"
PREVIOUS_REVISION = "20260909_0070"
TABLE = "recruiter_emails"
COLUMN = "content_dedupe_hash"
INDEX = "ix_recruiter_emails_content_dedupe_hash"


@contextmanager
def scratch_directory():
    """mkdtemp rather than TemporaryDirectory.

    SQLite on Windows keeps the file handle open until every engine alembic
    created is garbage-collected, and TemporaryDirectory's cleanup raises
    PermissionError when it is not.
    """
    directory = tempfile.mkdtemp()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


class ContentDedupeHashMigrationTests(unittest.TestCase):
    def _config(self) -> Config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        return config

    @contextmanager
    def _database_at(self, revision: str, *, drop_column: bool = True):
        """A database stamped at `revision`, with settings.database_url pointed at it.

        The url has to be set on `settings`, not on the Config: alembic/env.py
        overwrites sqlalchemy.url with settings.database_url, so a Config url is
        discarded and the real dev database gets migrated instead.
        """
        with scratch_directory() as directory:
            database_path = Path(directory) / "scratch.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            engine.dispose()

            connection = sqlite3.connect(database_path)
            if drop_column:
                # create_all() builds today's model, which already has the
                # column. Remove it so the migration has work to do - index
                # first, because SQLite refuses to drop a column an index still
                # references.
                connection.execute(f"DROP INDEX IF EXISTS {INDEX}")
                connection.execute(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}")
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute("INSERT INTO alembic_version(version_num) VALUES (?)", (revision,))
            connection.commit()
            connection.close()

            previous_url = settings.database_url
            settings.database_url = database_url
            try:
                yield database_url
            finally:
                settings.database_url = previous_url

    def _columns(self, database_url: str) -> dict[str, object]:
        engine = sa.create_engine(database_url)
        try:
            return {row["name"]: row for row in sa.inspect(engine).get_columns(TABLE)}
        finally:
            engine.dispose()

    def _indexes(self, database_url: str) -> set[str]:
        engine = sa.create_engine(database_url)
        try:
            return {index["name"] for index in sa.inspect(engine).get_indexes(TABLE)}
        finally:
            engine.dispose()

    def test_upgrade_adds_the_column_and_its_index(self) -> None:
        with self._database_at(PREVIOUS_REVISION) as database_url:
            self.assertNotIn(COLUMN, self._columns(database_url))
            command.upgrade(self._config(), REVISION)
            self.assertIn(COLUMN, self._columns(database_url))
            self.assertIn(INDEX, self._indexes(database_url))

    def test_the_column_is_nullable_so_existing_rows_are_untouched(self) -> None:
        with self._database_at(PREVIOUS_REVISION) as database_url:
            command.upgrade(self._config(), REVISION)
            self.assertTrue(self._columns(database_url)[COLUMN]["nullable"])

    def test_the_index_is_over_a_bounded_column(self) -> None:
        """Migration 0051 took production down indexing an unbounded Text column."""
        with self._database_at(PREVIOUS_REVISION) as database_url:
            command.upgrade(self._config(), REVISION)
            column_type = self._columns(database_url)[COLUMN]["type"]
        self.assertNotIsInstance(column_type, sa.Text)
        self.assertEqual(getattr(column_type, "length", None), 40)

    def test_upgrade_is_a_no_op_when_the_column_already_exists(self) -> None:
        """A create_all() database already carries it from the model."""
        with self._database_at(PREVIOUS_REVISION, drop_column=False) as database_url:
            command.upgrade(self._config(), REVISION)
            self.assertIn(COLUMN, self._columns(database_url))
            self.assertIn(INDEX, self._indexes(database_url))

    def test_downgrade_then_upgrade_again(self) -> None:
        """Boot runs `upgrade head` every time, including after a rollback."""
        with self._database_at(PREVIOUS_REVISION) as database_url:
            config = self._config()
            command.upgrade(config, REVISION)
            command.downgrade(config, PREVIOUS_REVISION)
            self.assertNotIn(COLUMN, self._columns(database_url))
            self.assertNotIn(INDEX, self._indexes(database_url))
            command.upgrade(config, REVISION)
            self.assertIn(COLUMN, self._columns(database_url))

    def test_the_migration_matches_the_model(self) -> None:
        declared = app.models.RecruiterEmail.__table__.columns[COLUMN]
        self.assertTrue(declared.nullable)
        self.assertEqual(declared.type.length, 40)


if __name__ == "__main__":
    unittest.main()
