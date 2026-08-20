import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class PremiumContactStatusMigrationTests(unittest.TestCase):
    def test_source_fields_are_backfilled_from_active_lead(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260818_0021")

                now = datetime.now(UTC).replace(tzinfo=None)
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    contact_id = connection.exec_driver_sql(
                        "INSERT INTO premium_number_contacts "
                        "(owner_id, normalized_phone_number, display_phone_number, is_recruiter, is_employer, "
                        "recruiter_name, designation, recruiter_email, owner_name, company, first_detected_email_id, "
                        "source_email_id, active_recruiter_lead_id, active_employer_lead_id, linkedin_url, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        ("owner", "12145551212", "+1 214 555 1212", True, False, "Rita", "Recruiter", "", "Unknown", "Agency", None, None, None, None, "", now, now),
                    ).lastrowid
                    lead_id = connection.exec_driver_sql(
                        "INSERT INTO premium_number_leads "
                        "(owner_id, recruiter_email_id, external_opportunity_id, contact_id, phone_number_normalized, "
                        "phone_number_display, role, extraction_source, contact_email, owner_name, company, designation, "
                        "purpose, confidence, contact_type, recruiter_relevance_score, is_recruiter_relevant, relevance_reason, "
                        "source_fragment, source_email_sender, source_email_subject, source_email_message_id, source_url, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        ("owner", None, 77, contact_id, "12145551212", "+1 214 555 1212", "recruiter", "nvoids", "", "Rita", "Agency", "Recruiter", "Recruiter contact", "high", "recruiter_direct", 95, True, "relevant", "", "", "", "nvoids:77", "https://example.test/post/77", now, now),
                    ).lastrowid
                    connection.exec_driver_sql(
                        "UPDATE premium_number_contacts SET active_recruiter_lead_id=? WHERE id=?",
                        (lead_id, contact_id),
                    )
                engine.dispose()

                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    row = connection.exec_driver_sql(
                        "SELECT source_type, source_id, source_link_url, deleted_at "
                        "FROM premium_number_contacts WHERE id=?",
                        (contact_id,),
                    ).mappings().one()
                    self.assertEqual(row["source_type"], "nvoids")
                    self.assertEqual(row["source_id"], 77)
                    self.assertEqual(row["source_link_url"], "https://example.test/post/77")
                    self.assertIsNone(row["deleted_at"])
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
