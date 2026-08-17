import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class MultiRoleMigrationTests(unittest.TestCase):
    def test_upgrade_from_0008_adds_multi_role_columns_and_unique_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            metadata = sa.MetaData()
            sa.Table("recruiter_emails", metadata, sa.Column("id", sa.Integer(), primary_key=True))
            sa.Table("user_settings", metadata, sa.Column("id", sa.Integer(), primary_key=True))
            sa.Table("recent_runs", metadata, sa.Column("id", sa.Integer(), primary_key=True))
            metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260713_0008')")

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "20260722_0009")
            finally:
                settings.database_url = previous_url
                engine.dispose()

            upgraded = sa.create_engine(database_url)
            inspector = sa.inspect(upgraded)
            email_columns = {column["name"] for column in inspector.get_columns("recruiter_emails")}
            settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
            index_names = {index["name"] for index in inspector.get_indexes("recruiter_emails")}
            self.assertIn("source_parent_email_id", email_columns)
            self.assertIn("sendability_status", email_columns)
            self.assertIn("feature_role_manifest_enabled", settings_columns)
            self.assertIn("ux_recruiter_email_parent_requirement", index_names)
            upgraded.dispose()


if __name__ == "__main__":
    unittest.main()
