import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ApplicationPhaseFourMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_phase_three_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260822_0028")
                command.upgrade(config, "20260823_0029")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                columns = {column["name"]: column for column in inspector.get_columns("user_settings")}
                flag = columns["feature_application_outreach_drafts_enabled"]
                self.assertFalse(flag["nullable"])
                self.assertIn(str(flag["default"]).lower(), {"0", "false", "(0)"})
                self.assertIn("application_suggestions", inspector.get_table_names())
                self.assertIn("application_rtrs", inspector.get_table_names())
                engine.dispose()

                command.downgrade(config, "20260822_0028")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                columns = {column["name"] for column in inspector.get_columns("user_settings")}
                self.assertNotIn("feature_application_outreach_drafts_enabled", columns)
                self.assertIn("application_suggestions", inspector.get_table_names())
                self.assertIn("application_rtrs", inspector.get_table_names())
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
