"""Migration 0063: two upload-metadata columns, upgrade/downgrade/re-upgrade.

Same three properties 0062 pins, for the same reason - `alembic upgrade head`
runs in the container command on backend boot: the adds are guarded so a re-run
is a no-op, existing rows read a sane value afterwards, and the revision is
reversible so it can be backed out of a boot loop.

`candidate_profile_uploaded_at` is the interesting one. It is nullable with no
server default, and NULL is the correct reading for every existing row: nobody
has uploaded a profile yet.
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

COLUMNS = ("candidate_profile_filename", "candidate_profile_uploaded_at")
PREVIOUS_REVISION = "20260904_0062"


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


class CandidateProfileUploadMigrationTests(unittest.TestCase):
    def _alembic_config(self) -> Config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        return config

    def _database_at_0062(self, directory: str, seed_owner: str | None = None) -> str:
        """A schema as it stood before 0063: every table, minus the new columns."""
        database_url = f"sqlite:///{(Path(directory) / 'migration.db').as_posix()}"
        engine = sa.create_engine(database_url)
        Base.metadata.create_all(engine)
        if seed_owner:
            # Through the ORM, before the columns are dropped: a raw INSERT would
            # have to satisfy every NOT NULL column on this very wide table.
            with sa.orm.Session(engine) as seed:
                seed.add(app.models.UserSettings(owner_id=seed_owner))
                seed.commit()
        with engine.begin() as connection:
            for column in COLUMNS:
                connection.exec_driver_sql(f"ALTER TABLE user_settings DROP COLUMN {column}")
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

    def test_0063_upgrades_downgrades_and_re_upgrades(self) -> None:
        with scratch_directory() as directory:
            database_url = self._database_at_0062(directory)
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url

                command.upgrade(config, "head")
                present = self._columns(database_url)
                for column in COLUMNS:
                    self.assertIn(column, present)
                # 0051's outage, pinned: nothing here is queried by value.
                self.assertFalse(set(COLUMNS) & self._indexed(database_url))

                # Boot runs `upgrade head` every time, so it must be idempotent.
                command.upgrade(config, "head")
                self.assertTrue(set(COLUMNS) <= self._columns(database_url))

                command.downgrade(config, PREVIOUS_REVISION)
                self.assertFalse(set(COLUMNS) & self._columns(database_url))
                # 0062's column must survive 0063's downgrade untouched.
                self.assertIn("candidate_profile_markdown", self._columns(database_url))

                command.upgrade(config, "head")
                self.assertTrue(set(COLUMNS) <= self._columns(database_url))
            finally:
                settings.database_url = previous_url

    def test_an_existing_row_reads_empty_filename_and_null_timestamp(self) -> None:
        with scratch_directory() as directory:
            database_url = self._database_at_0062(directory, seed_owner="existing-owner")
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    row = connection.exec_driver_sql(
                        "SELECT candidate_profile_filename, candidate_profile_uploaded_at "
                        "FROM user_settings WHERE owner_id='existing-owner'"
                    ).first()
                engine.dispose()
                self.assertEqual(row[0], "")
                # NULL, not an epoch: nobody has uploaded a profile yet, and a
                # zero timestamp would render as a real upload date in the panel.
                self.assertIsNone(row[1])
            finally:
                settings.database_url = previous_url


class ModelShapeTests(unittest.TestCase):
    def test_the_upload_columns_are_bounded_nullable_and_unindexed(self) -> None:
        columns = app.models.UserSettings.__table__.columns

        filename = columns["candidate_profile_filename"]
        self.assertIsInstance(filename.type, sa.String)
        self.assertEqual(filename.type.length, 255)
        self.assertFalse(filename.index)

        uploaded_at = columns["candidate_profile_uploaded_at"]
        self.assertTrue(uploaded_at.nullable)
        self.assertFalse(uploaded_at.index)


if __name__ == "__main__":
    unittest.main()
