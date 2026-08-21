import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ApplicationPhaseTwoMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_preserve_phase_one_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260820_0026")
                command.upgrade(config, "20260821_0027")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertIn("application_rtrs", inspector.get_table_names())
                self.assertIn("application_interviews", inspector.get_table_names())
                application_columns = {column["name"] for column in inspector.get_columns("applications")}
                self.assertIn("closed_reason_code", application_columns)
                opportunity_columns = {
                    column["name"]: column for column in inspector.get_columns("recruiter_opportunities")
                }
                self.assertTrue(
                    {
                        "employment_type",
                        "rate_amount",
                        "rate_currency",
                        "rate_unit",
                        "contract_duration",
                        "relocation_required",
                        "extension_likely",
                        "end_client_confirmed",
                        "job_confidence",
                    }.issubset(opportunity_columns)
                )
                self.assertFalse(opportunity_columns["employment_type"]["nullable"])
                self.assertTrue(opportunity_columns["relocation_required"]["nullable"])
                contact_columns = {
                    column["name"]: column for column in inspector.get_columns("premium_number_contacts")
                }
                self.assertTrue(
                    {
                        "recruiter_verification_level",
                        "do_not_work_again",
                        "do_not_work_again_reason",
                    }.issubset(contact_columns)
                )
                self.assertFalse(contact_columns["do_not_work_again"]["nullable"])
                engine.dispose()

                command.downgrade(config, "20260820_0026")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn("application_rtrs", inspector.get_table_names())
                self.assertNotIn("application_interviews", inspector.get_table_names())
                self.assertIn("applications", inspector.get_table_names())
                self.assertIn("application_events", inspector.get_table_names())
                self.assertNotIn(
                    "closed_reason_code",
                    {column["name"] for column in inspector.get_columns("applications")},
                )
                self.assertNotIn(
                    "employment_type",
                    {column["name"] for column in inspector.get_columns("recruiter_opportunities")},
                )
                self.assertNotIn(
                    "recruiter_verification_level",
                    {column["name"] for column in inspector.get_columns("premium_number_contacts")},
                )
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
