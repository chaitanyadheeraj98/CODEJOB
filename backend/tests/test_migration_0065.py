"""Migration 0065: the candidate_documents table.

`alembic upgrade head` runs in the docker-compose container command on every
backend boot, so the shape asserted here is the shape that reaches production
unattended: guarded creation, bounded indexed columns, and an unindexed Text
path (migration 0051 blew the Postgres btree key limit with an index over an
unbounded column).
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

REVISION = "20260904_0065"
PREVIOUS_REVISION = "20260904_0064"


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


class CandidateDocumentsMigrationTests(unittest.TestCase):
    def _alembic_config(self) -> Config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        return config

    @contextmanager
    def _database_at(self, revision: str):
        """A database stamped at `revision`, with settings.database_url pointed at it.

        The url has to be set on `settings`, not on the Config: alembic/env.py
        overwrites sqlalchemy.url with settings.database_url, so a Config url is
        discarded and the *real* dev database gets migrated instead.
        """
        with scratch_directory() as directory:
            database_path = Path(directory) / "scratch.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            connection = sqlite3.connect(database_path)
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

    def test_upgrade_creates_the_table_and_downgrade_removes_it(self) -> None:
        with self._database_at(PREVIOUS_REVISION) as database_url:
            config = self._alembic_config()
            command.upgrade(config, REVISION)

            engine = sa.create_engine(database_url)
            try:
                self.assertIn("candidate_documents", sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()

            command.downgrade(config, PREVIOUS_REVISION)
            engine = sa.create_engine(database_url)
            try:
                self.assertNotIn("candidate_documents", sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()

            # Boot runs `upgrade head` every time, including after a rollback.
            command.upgrade(config, REVISION)
            engine = sa.create_engine(database_url)
            try:
                self.assertIn("candidate_documents", sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()

    def test_upgrading_a_database_that_already_has_the_table_is_a_no_op(self) -> None:
        """The guard: create_all() databases already carry it from the model."""
        with self._database_at(PREVIOUS_REVISION) as database_url:
            engine = sa.create_engine(database_url)
            app.models.CandidateDocument.__table__.create(engine)
            engine.dispose()

            command.upgrade(self._alembic_config(), REVISION)

            engine = sa.create_engine(database_url)
            try:
                self.assertIn("candidate_documents", sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()

    def test_the_migration_matches_the_model(self) -> None:
        with self._database_at(PREVIOUS_REVISION) as database_url:
            command.upgrade(self._alembic_config(), REVISION)
            engine = sa.create_engine(database_url)
            try:
                migrated = {row["name"] for row in sa.inspect(engine).get_columns("candidate_documents")}
            finally:
                engine.dispose()
        declared = {column.name for column in app.models.CandidateDocument.__table__.columns}
        self.assertEqual(migrated, declared)

    def test_only_bounded_columns_are_indexed(self) -> None:
        """file_path is Text. Migration 0051 took prod down indexing one of those."""
        with self._database_at(PREVIOUS_REVISION) as database_url:
            command.upgrade(self._alembic_config(), REVISION)
            engine = sa.create_engine(database_url)
            try:
                inspector = sa.inspect(engine)
                indexed = {
                    column
                    for index in inspector.get_indexes("candidate_documents")
                    for column in index["column_names"]
                }
                types = {
                    row["name"]: row["type"] for row in inspector.get_columns("candidate_documents")
                }
            finally:
                engine.dispose()

        self.assertNotIn("file_path", indexed)
        for column in indexed:
            self.assertNotIsInstance(types[column], sa.Text, column)

    def test_the_model_carries_no_enabled_flag(self) -> None:
        """The distinction from attachment_assets, pinned.

        An is_enabled column here would recreate exactly the all-on/all-off
        behaviour these documents exist to avoid: nothing is sent unless the
        user names it in chat and confirms the card.
        """
        columns = {column.name for column in app.models.CandidateDocument.__table__.columns}
        self.assertNotIn("is_enabled", columns)
        self.assertIn("is_enabled", {column.name for column in app.models.AttachmentAsset.__table__.columns})

    def test_create_all_and_the_migration_agree(self) -> None:
        with scratch_directory() as directory:
            database_path = Path(directory) / "fresh.db"
            engine = sa.create_engine(f"sqlite:///{database_path.as_posix()}")
            Base.metadata.create_all(engine)
            try:
                self.assertIn("candidate_documents", sa.inspect(engine).get_table_names())
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
