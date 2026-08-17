import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings


class StrictCandidateScreeningMigrationTests(unittest.TestCase):
    def test_upgrade_from_0009_adds_default_off_toggle_and_nullable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            metadata = sa.MetaData()
            sa.Table("recruiter_emails", metadata, sa.Column("id", sa.Integer(), primary_key=True))
            sa.Table("user_settings", metadata, sa.Column("id", sa.Integer(), primary_key=True))
            metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260722_0009')")
                connection.exec_driver_sql("INSERT INTO user_settings(id) VALUES (1)")
                connection.exec_driver_sql("INSERT INTO recruiter_emails(id) VALUES (1)")

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "20260723_0010")
            finally:
                settings.database_url = previous_url
                engine.dispose()

            upgraded = sa.create_engine(database_url)
            inspector = sa.inspect(upgraded)
            settings_columns = {column["name"]: column for column in inspector.get_columns("user_settings")}
            email_columns = {column["name"]: column for column in inspector.get_columns("recruiter_emails")}
            with upgraded.connect() as connection:
                revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                toggle = connection.exec_driver_sql(
                    "SELECT feature_strict_candidate_screening_enabled FROM user_settings WHERE id = 1"
                ).scalar_one()
                mode = connection.exec_driver_sql(
                    "SELECT screening_mode FROM recruiter_emails WHERE id = 1"
                ).scalar_one_or_none()

            self.assertIn("feature_strict_candidate_screening_enabled", settings_columns)
            self.assertEqual(revision, "20260723_0010")
            self.assertFalse(settings_columns["feature_strict_candidate_screening_enabled"]["nullable"])
            self.assertIn("screening_mode", email_columns)
            self.assertTrue(email_columns["screening_mode"]["nullable"])
            self.assertEqual(toggle, 0)
            self.assertIsNone(mode)
            upgraded.dispose()

    def test_fresh_sqlite_upgrade_reaches_async_job_progress_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "fresh.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "head")
            finally:
                settings.database_url = previous_url

            engine = sa.create_engine(database_url)
            try:
                inspector = sa.inspect(engine)
                with engine.connect() as connection:
                    revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                self.assertEqual(revision, ScriptDirectory.from_config(config).get_current_head())
                self.assertIn(
                    "feature_strict_candidate_screening_enabled",
                    {column["name"] for column in inspector.get_columns("user_settings")},
                )
                self.assertIn(
                    "screening_mode",
                    {column["name"] for column in inspector.get_columns("recruiter_emails")},
                )
                recent_run_columns = {column["name"] for column in inspector.get_columns("recent_runs")}
                self.assertTrue(
                    {"job_backend_id", "total_items", "processed_items", "progress_pct", "queue_name"}
                    <= recent_run_columns
                )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
