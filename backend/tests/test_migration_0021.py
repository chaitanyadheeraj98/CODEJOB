import importlib.util
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import settings

MIGRATION_PATH = Path(__file__).parents[1] / "alembic" / "versions" / "20260818_0021_unified_contacts.py"


class UnifiedContactsMigrationTests(unittest.TestCase):
    def _prepare_pre_migration_database(self, directory: Path) -> tuple[Config, str]:
        database_path = directory / "unified-contacts.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        settings.database_url = database_url
        command.upgrade(config, "20260818_0020")
        return config, database_url

    def _load_migration_module(self):
        spec = importlib.util.spec_from_file_location("migration_0021", MIGRATION_PATH)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    def test_unified_contacts_migration_preserves_recruiter_id_for_opportunity_fk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_url = settings.database_url
            now = datetime.now(UTC).replace(tzinfo=None)
            try:
                config, database_url = self._prepare_pre_migration_database(Path(directory))
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO recruiter_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, recruiter_name, company, "
                        "designation, recruiter_email, first_detected_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (41, "owner", "12145551212", "(214) 555-1212", "Rita", "Agency", "Recruiter", "rita@agency.example", None, now, now),
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO recruiter_opportunities "
                        "(owner_id, recruiter_number_id, source_email_id, gmail_message_id, source_type, source_url, "
                        "external_opportunity_id, email_subject, email_sender, gmail_open_url, received_at, job_title, client, "
                        "location, work_mode, visa_restrictions, extracted_skills, evidence, status, notes, "
                        "cold_call_script, cold_call_script_updated_at, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        ("owner", 41, None, "gmail-1", "gmail", None, None, "Role", "sender@example.com", "", now, "Engineer", "Client Co", "Dallas", "Remote", "", "Python", "", "New", "", None, None, now, now),
                    )
                engine.dispose()

                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    contact = connection.exec_driver_sql(
                        "SELECT * FROM premium_number_contacts WHERE id=41"
                    ).mappings().one()
                    self.assertEqual(contact["normalized_phone_number"], "12145551212")
                    self.assertEqual(contact["recruiter_name"], "Rita")
                    self.assertEqual(
                        connection.exec_driver_sql(
                            "SELECT recruiter_number_id FROM recruiter_opportunities WHERE gmail_message_id='gmail-1'"
                        ).scalar_one(),
                        41,
                    )
                engine.dispose()
            finally:
                settings.database_url = previous_url

    def test_unified_contacts_migration_merges_shared_switchboard_number(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_url = settings.database_url
            now = datetime.now(UTC).replace(tzinfo=None)
            try:
                config, database_url = self._prepare_pre_migration_database(Path(directory))
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO recruiter_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, recruiter_name, company, "
                        "designation, recruiter_email, first_detected_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (41, "owner", "12145551212", "(214) 555-1212", "Rita", "Agency", "Recruiter", "rita@agency.example", None, now, now),
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO employer_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, owner_name, company, source_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (1, "owner", "12145551212", "(214) 555-1212", "Hiring Desk", "Client Co", None, now, now),
                    )
                engine.dispose()

                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    rows = connection.exec_driver_sql(
                        "SELECT * FROM premium_number_contacts WHERE normalized_phone_number='12145551212'"
                    ).mappings().all()
                    self.assertEqual(len(rows), 1)
                    merged = rows[0]
                    self.assertTrue(merged["is_recruiter"])
                    self.assertTrue(merged["is_employer"])
                    self.assertEqual(merged["owner_name"], "Hiring Desk")
                engine.dispose()
            finally:
                settings.database_url = previous_url

    def test_unified_contacts_migration_creates_employer_only_row_with_new_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_url = settings.database_url
            now = datetime.now(UTC).replace(tzinfo=None)
            try:
                config, database_url = self._prepare_pre_migration_database(Path(directory))
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO employer_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, owner_name, company, source_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (2, "owner", "19725551212", "(972) 555-1212", "Dana", "Employer Co", None, now, now),
                    )
                engine.dispose()

                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    employer_only = connection.exec_driver_sql(
                        "SELECT * FROM premium_number_contacts WHERE normalized_phone_number='19725551212'"
                    ).mappings().one()
                    self.assertFalse(employer_only["is_recruiter"])
                    self.assertTrue(employer_only["is_employer"])
                    self.assertNotEqual(employer_only["id"], 2)
                engine.dispose()
            finally:
                settings.database_url = previous_url

    def test_unified_contacts_migration_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous_url = settings.database_url
            now = datetime.now(UTC).replace(tzinfo=None)
            try:
                config, database_url = self._prepare_pre_migration_database(Path(directory))
                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    connection.exec_driver_sql(
                        "INSERT INTO recruiter_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, recruiter_name, company, "
                        "designation, recruiter_email, first_detected_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (41, "owner", "12145551212", "(214) 555-1212", "Rita", "Agency", "Recruiter", "rita@agency.example", None, now, now),
                    )
                    connection.exec_driver_sql(
                        "INSERT INTO employer_numbers "
                        "(id, owner_id, normalized_phone_number, display_phone_number, owner_name, company, source_email_id, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (2, "owner", "19725551212", "(972) 555-1212", "Dana", "Employer Co", None, now, now),
                    )
                engine.dispose()

                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    before_count = connection.exec_driver_sql(
                        "SELECT COUNT(*) FROM premium_number_contacts"
                    ).scalar_one()
                    self.assertEqual(before_count, 2)

                    migration = self._load_migration_module()
                    migration.op = Operations(MigrationContext.configure(connection))
                    migration.upgrade()

                    after_count = connection.exec_driver_sql(
                        "SELECT COUNT(*) FROM premium_number_contacts"
                    ).scalar_one()
                    self.assertEqual(after_count, before_count)
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
