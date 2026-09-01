import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import (
    Application,
    AppTSApplication,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
)
from tests.migration_support import drop_foreign_keys


class ApplicationRecordLinksMigrationTests(unittest.TestCase):
    def test_backfills_exact_links_promotions_and_unambiguous_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            with Session(engine) as session:
                resume = ResumeAsset(
                    owner_id="owner",
                    file_path="resume.pdf",
                    file_name="resume.pdf",
                    sha256="sha",
                    version=1,
                )
                ambiguous_a = ResumeAsset(
                    owner_id="owner",
                    file_path="duplicate-a.pdf",
                    file_name="duplicate.pdf",
                    sha256="sha-a",
                    version=1,
                )
                ambiguous_b = ResumeAsset(
                    owner_id="owner",
                    file_path="duplicate-b.pdf",
                    file_name="duplicate.pdf",
                    sha256="sha-b",
                    version=1,
                )
                contact = PremiumNumberContact(owner_id="owner", display_phone_number="", recruiter_name="Jane")
                session.add_all([resume, ambiguous_a, ambiguous_b, contact])
                session.flush()
                email = RecruiterEmail(
                    owner_id="owner",
                    sender="Jane <jane@example.com>",
                    subject="Role",
                    body="Body",
                    recipient_email="jane@example.com",
                    resume_asset_id=resume.id,
                    record_id="record-1",
                )
                session.add(email)
                session.flush()
                opportunity = RecruiterOpportunity(
                    owner_id="owner",
                    recruiter_number_id=contact.id,
                    source_email_id=email.id,
                    gmail_message_id="gmail-1",
                    resume_file_name=resume.file_name,
                    record_id="record-1",
                )
                ambiguous_opportunity = RecruiterOpportunity(
                    owner_id="owner",
                    recruiter_number_id=contact.id,
                    gmail_message_id="gmail-2",
                    resume_file_name="duplicate.pdf",
                )
                session.add_all([opportunity, ambiguous_opportunity])
                session.flush()
                legacy = Application(
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot=resume.file_name,
                    resume_sha256_snapshot=resume.sha256,
                    recruiter_opportunity_id=opportunity.id,
                    recruiter_contact_id=contact.id,
                    dedupe_key="legacy",
                )
                session.add(legacy)
                session.flush()
                tracked = AppTSApplication(
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot=resume.file_name,
                    resume_sha256_snapshot=resume.sha256,
                    manual_source_note=f"Tracked from recruiter opportunity {opportunity.id}",
                    dedupe_key="tracked",
                )
                promoted = AppTSApplication(
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot=resume.file_name,
                    resume_sha256_snapshot=resume.sha256,
                    dedupe_key=f"appts_promoted:{legacy.id}",
                )
                untouched = AppTSApplication(
                    owner_id="owner",
                    resume_asset_id=resume.id,
                    resume_version_snapshot=1,
                    resume_file_name_snapshot=resume.file_name,
                    resume_sha256_snapshot=resume.sha256,
                    manual_source_note="edited note",
                    dedupe_key="untouched",
                )
                session.add_all([tracked, promoted, untouched])
                session.commit()
                ids = {
                    "resume": resume.id,
                    "opportunity": opportunity.id,
                    "ambiguous_opportunity": ambiguous_opportunity.id,
                    "email": email.id,
                    "contact": contact.id,
                    "legacy": legacy.id,
                    "tracked": tracked.id,
                    "promoted": promoted.id,
                    "untouched": untouched.id,
                }

            drop_foreign_keys(
                engine, "recruiter_opportunities", "fk_recruiter_opportunities_resume_asset"
            )
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP INDEX IF EXISTS ix_applications_promoted_to_appts_application_id")
                connection.exec_driver_sql("ALTER TABLE applications DROP COLUMN promoted_to_appts_application_id")
                connection.exec_driver_sql("DROP INDEX IF EXISTS ix_recruiter_opportunities_resume_asset_id")
                connection.exec_driver_sql("ALTER TABLE recruiter_opportunities DROP COLUMN resume_asset_id")
                connection.exec_driver_sql("DROP INDEX IF EXISTS ix_productivity_events_entity_type")
                connection.exec_driver_sql("ALTER TABLE productivity_events DROP COLUMN entity_type")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260910_0048')")
            engine.dispose()

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "head")
                upgraded = sa.create_engine(database_url)
                with Session(upgraded) as session:
                    tracked = session.get(AppTSApplication, ids["tracked"])
                    self.assertEqual(tracked.recruiter_opportunity_id, ids["opportunity"])
                    self.assertEqual(tracked.recruiter_contact_id, ids["contact"])
                    self.assertEqual(tracked.source_recruiter_email_id, ids["email"])
                    promoted = session.get(AppTSApplication, ids["promoted"])
                    self.assertEqual(promoted.recruiter_opportunity_id, ids["opportunity"])
                    self.assertEqual(session.get(Application, ids["legacy"]).promoted_to_appts_application_id, promoted.id)
                    self.assertIsNone(session.get(AppTSApplication, ids["untouched"]).recruiter_opportunity_id)
                    self.assertEqual(session.get(RecruiterOpportunity, ids["opportunity"]).resume_asset_id, ids["resume"])
                    self.assertIsNone(session.get(RecruiterOpportunity, ids["ambiguous_opportunity"]).resume_asset_id)
                    self.assertEqual(session.get(RecruiterEmail, ids["email"]).resolved_recruiter_email, "jane@example.com")
                upgraded.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
