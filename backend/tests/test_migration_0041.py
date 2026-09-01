import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import AppTSApplication, RecruiterEmail
from tests.migration_support import drop_foreign_keys


class AppTSSourceRecruiterEmailBackfillTests(unittest.TestCase):
    """Simulates a database from before revision 0041: real appts_applications rows
    tracked from emails (dedupe_key = 'appts_email:<id>'), manual/opportunity rows
    with no email link, and one row whose linked email was since deleted."""

    def test_upgrade_backfills_historical_rows_and_downgrade_removes_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)

            with Session(engine) as session:
                linked_email = RecruiterEmail(sender="recruiter@agency.com", subject="Java role", body="body text", ats_score=82.0)
                session.add(linked_email)
                session.flush()

                tracked_from_email = AppTSApplication(
                    resume_asset_id=1, resume_version_snapshot=1, resume_file_name_snapshot="resume.docx",
                    resume_sha256_snapshot="sha", dedupe_key=f"appts_email:{linked_email.id}",
                )
                tracked_with_deleted_email = AppTSApplication(
                    resume_asset_id=1, resume_version_snapshot=1, resume_file_name_snapshot="resume.docx",
                    resume_sha256_snapshot="sha", dedupe_key="appts_email:999999",
                )
                manual_entry = AppTSApplication(
                    resume_asset_id=1, resume_version_snapshot=1, resume_file_name_snapshot="resume.docx",
                    resume_sha256_snapshot="sha", dedupe_key="appts_manual:legacy-1",
                )
                session.add_all([tracked_from_email, tracked_with_deleted_email, manual_entry])
                session.commit()
                linked_email_id = linked_email.id
                tracked_from_email_id = tracked_from_email.id
                tracked_with_deleted_email_id = tracked_with_deleted_email.id
                manual_entry_id = manual_entry.id

            drop_foreign_keys(
                engine, "appts_applications", "fk_appts_applications_source_recruiter_email"
            )
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX IF EXISTS ix_appts_applications_source_recruiter_email_id")
                connection.exec_driver_sql("ALTER TABLE appts_applications DROP COLUMN source_recruiter_email_id")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260903_0040')")
            engine.dispose()

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "head")

                upgraded = sa.create_engine(database_url)
                columns = {column["name"] for column in sa.inspect(upgraded).get_columns("appts_applications")}
                self.assertIn("source_recruiter_email_id", columns)
                with Session(upgraded) as session:
                    self.assertEqual(session.get(AppTSApplication, tracked_from_email_id).source_recruiter_email_id, linked_email_id)
                    self.assertIsNone(session.get(AppTSApplication, tracked_with_deleted_email_id).source_recruiter_email_id)
                    self.assertIsNone(session.get(AppTSApplication, manual_entry_id).source_recruiter_email_id)
                upgraded.dispose()

                command.downgrade(config, "20260903_0040")
                downgraded = sa.create_engine(database_url)
                self.assertNotIn(
                    "source_recruiter_email_id",
                    {column["name"] for column in sa.inspect(downgraded).get_columns("appts_applications")},
                )
                downgraded.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
