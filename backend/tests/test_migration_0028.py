import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ApplicationPhaseThreeMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_phase_two_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260821_0027")
                command.upgrade(config, "20260822_0028")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertIn("application_suggestions", inspector.get_table_names())
                suggestion_columns = {column["name"]: column for column in inspector.get_columns("application_suggestions")}
                self.assertTrue(
                    {
                        "application_id",
                        "suggestion_type",
                        "status",
                        "confidence",
                        "reply_message_id",
                        "suggested_status",
                        "suggested_next_action_at",
                        "reason",
                        "resolved_at",
                    }.issubset(suggestion_columns)
                )
                self.assertFalse(suggestion_columns["status"]["nullable"])
                settings_columns = {column["name"]: column for column in inspector.get_columns("user_settings")}
                self.assertTrue(
                    {
                        "preferred_employment_types_json",
                        "preferred_minimum_rate",
                        "feature_application_automation_enabled",
                        "feature_reminder_sweep_interval_minutes",
                    }.issubset(settings_columns)
                )
                self.assertFalse(settings_columns["preferred_employment_types_json"]["nullable"])
                self.assertTrue(settings_columns["preferred_minimum_rate"]["nullable"])
                self.assertIn("application_rtrs", inspector.get_table_names())
                engine.dispose()

                command.downgrade(config, "20260821_0027")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn("application_suggestions", inspector.get_table_names())
                self.assertIn("application_rtrs", inspector.get_table_names())
                self.assertIn("application_interviews", inspector.get_table_names())
                settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
                self.assertNotIn("feature_application_automation_enabled", settings_columns)
                self.assertNotIn("preferred_employment_types_json", settings_columns)
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
