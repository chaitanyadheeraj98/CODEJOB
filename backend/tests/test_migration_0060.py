"""Migration 0060: three tables and four columns, upgrade/downgrade/re-upgrade.

`alembic upgrade head` runs on backend boot. Migration 0051 took production down
with an index over an unbounded Text column, so every index this revision
creates is asserted to be on a fixed-width, FK, boolean, or datetime column, and
the five Text columns are named explicitly rather than checked by shape.

The downgrade/re-upgrade cycle is not ceremony: a migration that cannot be
reversed cannot be backed out of a boot loop.
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

from app.config import settings
from app.db import Base
# Imported for the side effect: without it Base.metadata is empty when this file
# runs alone, create_all() makes no tables, and the DROPs below fail.
import app.models  # noqa: F401

TABLES = ("scheduled_tasks", "scheduled_task_runs", "scheduled_task_items")

# Text on the model, and never indexable.
TEXT_COLUMNS = {"condition_json", "action_json", "prepared_json", "last_error", "error"}

EXPECTED_INDEXED = {
    "scheduled_tasks": {"id", "owner_id", "kind", "status", "next_run_at"},
    "scheduled_task_runs": {"id", "owner_id", "task_id", "started_at", "outcome", "expires_at"},
    "scheduled_task_items": {"id", "owner_id", "task_id", "done"},
}

# Every table v3 and v4 added, dropped before the migration runs so alembic
# builds them rather than finding them already present.
DROP_FIRST = (
    "scheduled_task_items",
    "scheduled_task_runs",
    "scheduled_tasks",
    "opportunity_cluster_members",
    "opportunity_clusters",
    "relationship_labels",
    "relationship_judgments",
)


@contextmanager
def scratch_directory():
    """mkdtemp rather than TemporaryDirectory.

    SQLite on Windows keeps the file handle open until every engine alembic
    created is garbage-collected, and TemporaryDirectory's cleanup raises
    PermissionError when it is not. The directory is scratch either way.
    """
    directory = tempfile.mkdtemp()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


# create_all() builds the model's own indexes, so a column carrying index=True
# cannot be dropped until its index is gone.
DROP_INDEXES_FIRST = ("ix_application_suggestions_expires_at",)


class SchedulingMigrationTests(unittest.TestCase):
    def indexed_columns(self, inspector: sa.Inspector, table: str) -> set[str]:
        return {
            column
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
            if column is not None
        }

    def columns(self, inspector: sa.Inspector, table: str) -> set[str]:
        return {row["name"] for row in inspector.get_columns(table)}

    def test_0060_upgrades_downgrades_and_re_upgrades(self) -> None:
        with scratch_directory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                for table in DROP_FIRST:
                    connection.exec_driver_sql(f"DROP TABLE {table}")
                # The two column pairs 0060 adds must not already exist.
                for index in DROP_INDEXES_FIRST:
                    connection.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
                for table, column in (
                    ("user_settings", "timezone"),
                    ("user_settings", "feature_scheduling_sweep_interval_minutes"),
                    ("application_suggestions", "expires_at"),
                    ("application_suggestions", "expiry_reason"),
                ):
                    connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
                connection.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version(version_num) VALUES ('20260918_0056')"
                )
            engine.dispose()

            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                names = set(inspector.get_table_names())
                for table in TABLES:
                    self.assertIn(table, names)
                    indexed = self.indexed_columns(inspector, table)
                    self.assertEqual(indexed, EXPECTED_INDEXED[table], table)
                    # 0051's failure mode, pinned per table.
                    self.assertFalse(indexed & TEXT_COLUMNS, table)

                self.assertIn("timezone", self.columns(inspector, "user_settings"))
                self.assertIn(
                    "feature_scheduling_sweep_interval_minutes",
                    self.columns(inspector, "user_settings"),
                )
                suggestion_columns = self.columns(inspector, "application_suggestions")
                self.assertIn("expires_at", suggestion_columns)
                self.assertIn("expiry_reason", suggestion_columns)
                self.assertIn(
                    "expires_at", self.indexed_columns(inspector, "application_suggestions")
                )
                engine.dispose()

                # Boot runs `upgrade head` every time, so it must be idempotent.
                command.upgrade(config, "head")

                command.downgrade(config, "20260921_0059")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                remaining = set(inspector.get_table_names())
                for table in TABLES:
                    self.assertNotIn(table, remaining)
                self.assertNotIn("timezone", self.columns(inspector, "user_settings"))
                engine.dispose()

                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                restored = set(sa.inspect(engine).get_table_names())
                for table in TABLES:
                    self.assertIn(table, restored)
                engine.dispose()
            finally:
                settings.database_url = previous_url

    def test_an_existing_settings_row_reads_utc_after_the_upgrade(self) -> None:
        """The server_default is what makes the column safe to add to a live table."""
        with scratch_directory() as directory:
            database_path = Path(directory) / "default.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            # Seed through the ORM, before the columns are dropped: a raw INSERT
            # would have to satisfy every NOT NULL column on this wide table.
            with sa.orm.Session(engine) as seed:
                seed.add(app.models.UserSettings(owner_id="existing-owner"))
                seed.commit()
            with engine.begin() as connection:
                for table in DROP_FIRST:
                    connection.exec_driver_sql(f"DROP TABLE {table}")
                for index in DROP_INDEXES_FIRST:
                    connection.exec_driver_sql(f"DROP INDEX IF EXISTS {index}")
                for table, column in (
                    ("user_settings", "timezone"),
                    ("user_settings", "feature_scheduling_sweep_interval_minutes"),
                    ("application_suggestions", "expires_at"),
                    ("application_suggestions", "expiry_reason"),
                ):
                    connection.exec_driver_sql(f"ALTER TABLE {table} DROP COLUMN {column}")
                connection.exec_driver_sql(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO alembic_version(version_num) VALUES ('20260921_0059')"
                )
            engine.dispose()

            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    row = connection.exec_driver_sql(
                        "SELECT timezone, feature_scheduling_sweep_interval_minutes "
                        "FROM user_settings WHERE owner_id='existing-owner'"
                    ).first()
                self.assertEqual(row[0], "UTC")
                self.assertEqual(row[1], 15)
                engine.dispose()
            finally:
                settings.database_url = previous_url


class ModelShapeTests(unittest.TestCase):
    def test_checklist_text_is_bounded_so_it_stays_indexable(self) -> None:
        column = app.models.ScheduledTaskItem.__table__.columns["text"]

        self.assertIsInstance(column.type, sa.String)
        self.assertEqual(column.type.length, 500)

    def test_the_json_columns_are_text_and_unindexed(self) -> None:
        task = app.models.ScheduledTask.__table__.columns
        run = app.models.ScheduledTaskRun.__table__.columns

        for column in (task["condition_json"], task["action_json"], task["last_error"],
                       run["prepared_json"], run["error"]):
            self.assertIsInstance(column.type, sa.Text)
            self.assertFalse(column.index)

    def test_deleting_a_task_cascades_its_runs_and_items(self) -> None:
        run_fk = list(app.models.ScheduledTaskRun.__table__.columns["task_id"].foreign_keys)[0]
        item_fk = list(app.models.ScheduledTaskItem.__table__.columns["task_id"].foreign_keys)[0]

        self.assertEqual(run_fk.ondelete, "CASCADE")
        self.assertEqual(item_fk.ondelete, "CASCADE")

    def test_the_run_outcome_vocabulary_is_a_single_source_of_truth(self) -> None:
        self.assertIn("notified", app.models.SCHEDULED_RUN_OUTCOMES)
        self.assertIn("partially_approved", app.models.SCHEDULED_RUN_OUTCOMES)
        self.assertIn("expired", app.models.SCHEDULED_RUN_OUTCOMES)
        # Drafted messages must never supersede: each targets different work.
        self.assertEqual(app.models.SUPERSEDING_KINDS, ("digest", "monitor"))
        self.assertIn("checklist", app.models.SCHEDULED_TASK_KINDS)
        self.assertIn("none", app.models.SCHEDULED_SCHEDULE_KINDS)


if __name__ == "__main__":
    unittest.main()
