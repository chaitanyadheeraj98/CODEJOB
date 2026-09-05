"""Migration 0062: one settings column, upgrade/downgrade/re-upgrade.

`alembic upgrade head` runs in the container command on backend boot, so the
three things that matter are all about *not* taking the backend down: the add is
guarded so a re-run is a no-op, an existing settings row reads `''` rather than
NULL afterwards, and the revision is reversible so it can be backed out of a
boot loop.

Stamped at 0061 rather than an older revision, so this run exercises only 0062 -
every table `create_all()` builds is already where the earlier revisions expect.
"""

import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
import sqlalchemy.orm
from alembic import command
from alembic.config import Config

import app.models  # noqa: F401  - populates Base.metadata
from app.config import settings
from app.db import Base

COLUMN = "candidate_profile_markdown"
PREVIOUS_REVISION = "20260904_0061"


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


class CandidateProfileMigrationTests(unittest.TestCase):
    def _alembic_config(self) -> Config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        return config

    def _database_at_0061(self, directory: str, seed_owner: str | None = None) -> str:
        """A schema as it stood before 0062: every table, minus the new column."""
        database_url = f"sqlite:///{(Path(directory) / 'migration.db').as_posix()}"
        engine = sa.create_engine(database_url)
        Base.metadata.create_all(engine)
        if seed_owner:
            # Through the ORM, before the column is dropped: a raw INSERT would
            # have to satisfy every NOT NULL column on this very wide table.
            with sa.orm.Session(engine) as seed:
                seed.add(app.models.UserSettings(owner_id=seed_owner))
                seed.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql(f"ALTER TABLE user_settings DROP COLUMN {COLUMN}")
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.exec_driver_sql(
                f"INSERT INTO alembic_version(version_num) VALUES ('{PREVIOUS_REVISION}')"
            )
        engine.dispose()
        return database_url

    def _columns(self, database_url: str) -> set[str]:
        engine = sa.create_engine(database_url)
        try:
            return {row["name"] for row in sa.inspect(engine).get_columns("user_settings")}
        finally:
            engine.dispose()

    def _indexed(self, database_url: str) -> set[str]:
        engine = sa.create_engine(database_url)
        try:
            return {
                column
                for index in sa.inspect(engine).get_indexes("user_settings")
                for column in index["column_names"]
                if column is not None
            }
        finally:
            engine.dispose()

    def test_0062_upgrades_downgrades_and_re_upgrades(self) -> None:
        with scratch_directory() as directory:
            database_url = self._database_at_0061(directory)
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url

                command.upgrade(config, "head")
                self.assertIn(COLUMN, self._columns(database_url))
                # 0051's outage, pinned: the profile is unbounded Text, and a
                # btree over it exceeds Postgres's 2704-byte key limit on real
                # data. Nothing queries it by value, so nothing should index it.
                self.assertNotIn(COLUMN, self._indexed(database_url))

                # Boot runs `upgrade head` every time, so it must be idempotent.
                command.upgrade(config, "head")
                self.assertIn(COLUMN, self._columns(database_url))

                command.downgrade(config, PREVIOUS_REVISION)
                self.assertNotIn(COLUMN, self._columns(database_url))

                command.upgrade(config, "head")
                self.assertIn(COLUMN, self._columns(database_url))
            finally:
                settings.database_url = previous_url

    def test_an_existing_settings_row_reads_empty_after_the_upgrade(self) -> None:
        """The server_default is what makes the column safe to add to a live table.

        Without it the existing row reads NULL, and the chat prompt builder would
        be handed None where it expects a string.
        """
        with scratch_directory() as directory:
            database_url = self._database_at_0061(directory, seed_owner="existing-owner")
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    row = connection.exec_driver_sql(
                        f"SELECT {COLUMN} FROM user_settings WHERE owner_id='existing-owner'"
                    ).first()
                engine.dispose()
                self.assertEqual(row[0], "")
            finally:
                settings.database_url = previous_url


class ModelShapeTests(unittest.TestCase):
    def test_the_profile_is_unbounded_text_and_unindexed(self) -> None:
        column = app.models.UserSettings.__table__.columns[COLUMN]

        # String(255) would truncate a real profile; an index would be the 0051
        # outage again. Both properties are load-bearing.
        self.assertIsInstance(column.type, sa.Text)
        self.assertFalse(column.index)


if __name__ == "__main__":
    unittest.main()
