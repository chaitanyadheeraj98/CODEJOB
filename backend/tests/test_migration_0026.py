import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ApplicationsMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_create_phase_one_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260820_0025")
                command.upgrade(config, "20260820_0026")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertIn("applications", inspector.get_table_names())
                self.assertIn("application_events", inspector.get_table_names())
                settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
                self.assertIn("feature_applications_enabled", settings_columns)
                unique_sets = {
                    tuple(sorted(item["column_names"]))
                    for item in inspector.get_indexes("applications")
                    if item.get("unique")
                }
                unique_sets.update(
                    tuple(sorted(item["column_names"]))
                    for item in inspector.get_unique_constraints("applications")
                    if item.get("column_names")
                )
                self.assertIn(
                    tuple(sorted(("owner_id", "resume_asset_id", "recruiter_opportunity_id"))),
                    unique_sets,
                )
                engine.dispose()

                command.downgrade(config, "20260820_0025")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn("applications", inspector.get_table_names())
                self.assertNotIn("application_events", inspector.get_table_names())
                settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
                self.assertNotIn("feature_applications_enabled", settings_columns)
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
