import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ResumeTrackingMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_resume_tracking_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'migration.db').as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260826_0032")
                command.upgrade(config, "20260827_0033")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertTrue(
                    {"application_skill_gap_snapshots", "application_outreach_messages"}.issubset(
                        inspector.get_table_names()
                    )
                )
                application_columns = {column["name"]: column for column in inspector.get_columns("applications")}
                self.assertTrue(
                    {
                        "manual_recruiter_email",
                        "resume_submission_status",
                        "resume_submitted_at",
                        "dedupe_key",
                        "resume_skills_snapshot_json",
                        "milestones_reached_json",
                    }.issubset(application_columns)
                )
                self.assertTrue(application_columns["recruiter_opportunity_id"]["nullable"])
                self.assertTrue(application_columns["recruiter_contact_id"]["nullable"])
                unique_sets = {
                    tuple(item["column_names"])
                    for item in inspector.get_unique_constraints("applications")
                    if item.get("column_names")
                }
                self.assertIn(("owner_id", "dedupe_key"), unique_sets)
                self.assertNotIn(("owner_id", "resume_asset_id", "recruiter_opportunity_id"), unique_sets)
                settings_columns = {column["name"] for column in inspector.get_columns("user_settings")}
                self.assertIn("feature_resume_tracking_enabled", settings_columns)
                engine.dispose()

                command.downgrade(config, "20260826_0032")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn("application_skill_gap_snapshots", inspector.get_table_names())
                self.assertNotIn("application_outreach_messages", inspector.get_table_names())
                self.assertNotIn(
                    "feature_resume_tracking_enabled",
                    {column["name"] for column in inspector.get_columns("user_settings")},
                )
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
