import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import (
    AppTSApplication,
    EmailConversation,
    PremiumNumberContact,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
)


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    return config


class LinkConstraintMigrationTests(unittest.TestCase):
    """The abort guard in migration 0050 cannot be rehearsed against real data - the
    host database is empty - so the threshold behaviour is pinned here instead."""

    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.database_path = Path(self._directory.name) / "migration.db"
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        previous_url = settings.database_url
        settings.database_url = self.database_url
        self.addCleanup(setattr, settings, "database_url", previous_url)

    def _engine(self, *, enforce_foreign_keys: bool = False) -> sa.Engine:
        engine = sa.create_engine(self.database_url)
        if enforce_foreign_keys:

            @sa.event.listens_for(engine, "connect")
            def _pragma(dbapi_connection, _record) -> None:
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        self.addCleanup(engine.dispose)
        return engine

    def _seed(self, session: Session) -> dict[str, int]:
        resume = ResumeAsset(
            owner_id="owner", file_path="resume.pdf", file_name="resume.pdf", sha256="sha", version=1
        )
        contact = PremiumNumberContact(owner_id="owner", display_phone_number="", recruiter_name="Jane")
        session.add_all([resume, contact])
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
            resume_asset_id=resume.id,
            record_id="record-1",
        )
        session.add(opportunity)
        session.flush()
        return {
            "resume": resume.id,
            "contact": contact.id,
            "email": email.id,
            "opportunity": opportunity.id,
        }

    def _application(
        self, ids: dict[str, int], *, dedupe_key: str, opportunity_id: int | None
    ) -> AppTSApplication:
        return AppTSApplication(
            owner_id="owner",
            resume_asset_id=ids["resume"],
            resume_version_snapshot=1,
            resume_file_name_snapshot="resume.pdf",
            resume_sha256_snapshot="sha",
            recruiter_opportunity_id=opportunity_id,
            recruiter_contact_id=ids["contact"],
            source_recruiter_email_id=ids["email"],
            dedupe_key=dedupe_key,
        )

    def _build_pre_0050_schema(self, seed) -> dict[str, int]:
        """create_all() already carries the 0050 constraints, so build the schema, run
        the migration downgrade to strip them, and leave the database at 0049."""
        engine = self._engine()
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            ids = seed(session)
            session.commit()
        engine.dispose()
        config = _alembic_config()
        command.stamp(config, "20260912_0050")
        command.downgrade(config, "20260911_0049")
        return ids

    def test_clean_links_gain_constraints_and_aggregate_indexes(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            session.add(self._application(ids, dedupe_key="clean", opportunity_id=ids["opportunity"]))
            return ids

        ids = self._build_pre_0050_schema(seed)
        command.upgrade(_alembic_config(), "head")

        inspector = sa.inspect(self._engine())
        expected = {
            "applications": {
                "fk_applications_recruiter_opportunity",
                "fk_applications_recruiter_contact",
            },
            "appts_applications": {
                "fk_appts_applications_recruiter_opportunity",
                "fk_appts_applications_recruiter_contact",
                "fk_appts_applications_source_recruiter_email",
            },
            "recruiter_opportunities": {"fk_recruiter_opportunities_resume_asset"},
            "email_conversations": {"fk_email_conversations_root_recruiter_email"},
        }
        for table, names in expected.items():
            actual = {fk["name"] for fk in inspector.get_foreign_keys(table)}
            self.assertTrue(names <= actual, f"{table} missing {names - actual}")

        for index_name, table in (
            ("ix_appts_applications_owner_contact_created", "appts_applications"),
            ("ix_email_reply_messages_conversation_direction_received", "email_reply_messages"),
            ("ix_recruiter_emails_owner_resolved_email_sent", "recruiter_emails"),
        ):
            self.assertIn(index_name, {index["name"] for index in inspector.get_indexes(table)})

        with Session(self._engine()) as session:
            row = session.query(AppTSApplication).filter_by(dedupe_key="clean").one()
            self.assertEqual(row.recruiter_opportunity_id, ids["opportunity"])

    def test_small_dangling_fraction_is_nulled_not_aborted(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            for index in range(25):
                session.add(
                    self._application(ids, dedupe_key=f"good-{index}", opportunity_id=ids["opportunity"])
                )
            session.add(self._application(ids, dedupe_key="dangling", opportunity_id=999_999))
            return ids

        ids = self._build_pre_0050_schema(seed)
        command.upgrade(_alembic_config(), "head")

        with Session(self._engine()) as session:
            dangling = session.query(AppTSApplication).filter_by(dedupe_key="dangling").one()
            self.assertIsNone(dangling.recruiter_opportunity_id)
            intact = session.query(AppTSApplication).filter_by(dedupe_key="good-0").one()
            self.assertEqual(intact.recruiter_opportunity_id, ids["opportunity"])

    def test_large_dangling_fraction_aborts_the_migration(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            session.add(self._application(ids, dedupe_key="dangling", opportunity_id=999_999))
            return ids

        self._build_pre_0050_schema(seed)
        with self.assertRaises(RuntimeError) as caught:
            command.upgrade(_alembic_config(), "head")
        self.assertIn("dangling reference", str(caught.exception))

        # The survey runs before any mutation, so the bad value survives for review.
        with Session(self._engine()) as session:
            row = session.query(AppTSApplication).filter_by(dedupe_key="dangling").one()
            self.assertEqual(row.recruiter_opportunity_id, 999_999)

    def test_not_null_root_email_dangling_aborts_instead_of_nulling(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            session.add(
                EmailConversation(
                    owner_id="owner", root_recruiter_email_id=999_999, external_thread_id="thread-1"
                )
            )
            return ids

        self._build_pre_0050_schema(seed)
        with self.assertRaises(RuntimeError) as caught:
            command.upgrade(_alembic_config(), "head")
        self.assertIn("NOT NULL", str(caught.exception))

    def test_deleting_an_opportunity_nulls_the_application_link(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            session.add(self._application(ids, dedupe_key="clean", opportunity_id=ids["opportunity"]))
            return ids

        ids = self._build_pre_0050_schema(seed)
        command.upgrade(_alembic_config(), "head")

        engine = self._engine(enforce_foreign_keys=True)
        with Session(engine) as session:
            session.delete(session.get(RecruiterOpportunity, ids["opportunity"]))
            session.commit()
            row = session.query(AppTSApplication).filter_by(dedupe_key="clean").one()
            self.assertIsNone(row.recruiter_opportunity_id)
            # The application survives; only the pointer is cleared.
            self.assertEqual(row.recruiter_contact_id, ids["contact"])

    def test_deleting_a_resume_nulls_the_opportunity_resume_link(self) -> None:
        ids = self._build_pre_0050_schema(self._seed)
        command.upgrade(_alembic_config(), "head")

        engine = self._engine(enforce_foreign_keys=True)
        with Session(engine) as session:
            session.execute(sa.text("DELETE FROM resume_assets WHERE id = :id"), {"id": ids["resume"]})
            session.commit()
            self.assertIsNone(session.get(RecruiterOpportunity, ids["opportunity"]).resume_asset_id)

    def test_root_email_of_a_conversation_cannot_be_deleted(self) -> None:
        def seed(session: Session) -> dict[str, int]:
            ids = self._seed(session)
            session.add(
                EmailConversation(
                    owner_id="owner", root_recruiter_email_id=ids["email"], external_thread_id="thread-1"
                )
            )
            return ids

        ids = self._build_pre_0050_schema(seed)
        command.upgrade(_alembic_config(), "head")

        engine = self._engine(enforce_foreign_keys=True)
        with Session(engine) as session:
            with self.assertRaises(IntegrityError):
                session.execute(
                    sa.text("DELETE FROM recruiter_emails WHERE id = :id"), {"id": ids["email"]}
                )
                session.commit()


if __name__ == "__main__":
    unittest.main()
