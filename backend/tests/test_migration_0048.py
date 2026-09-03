import json
import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import ContactIdentityAction, PremiumContactEmail, PremiumNumberContact

OWNER_ID = "owner-1"


def _contact(**kwargs) -> PremiumNumberContact:
    return PremiumNumberContact(owner_id=OWNER_ID, display_phone_number="", **kwargs)


class ContactEmailRoleSyncMigrationTests(unittest.TestCase):
    """0048 adds premium_contact_emails.role, backfills it, adopts headline emails that
    have no child row, and REPORTS - without touching - headline/child ownership
    mismatches and stale phone claims on soft-deleted contacts."""

    def _run(self, seed):
        """Build a pre-0048 database, seed it, stamp it at 0047, upgrade, hand back the
        engine plus a config that can downgrade again."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database_path = Path(directory.name) / "migration.db"
        database_url = f"sqlite:///{database_path.as_posix()}"

        engine = sa.create_engine(database_url)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            seed(session)
            session.commit()
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE premium_contact_emails DROP COLUMN role")
            connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260909_0047')")
        engine.dispose()

        previous_url = settings.database_url
        settings.database_url = database_url
        self.addCleanup(setattr, settings, "database_url", previous_url)

        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        command.upgrade(config, "head")
        upgraded = sa.create_engine(database_url)
        self.addCleanup(upgraded.dispose)
        return upgraded, config, database_url

    def test_adds_role_column_and_downgrade_removes_it(self) -> None:
        state = {}

        def seed(session: Session) -> None:
            contact = _contact(recruiter_name="Ada", company="Acme", is_recruiter=True)
            session.add(contact)
            session.flush()
            row = PremiumContactEmail(
                owner_id=OWNER_ID, premium_contact_id=contact.id,
                normalized_email="ada@acme.com", domain="acme.com", is_primary=True,
            )
            session.add(row)
            session.flush()
            state["email_id"] = row.id

        engine, config, _ = self._run(seed)
        self.assertIn("role", {c["name"] for c in sa.inspect(engine).get_columns("premium_contact_emails")})
        with Session(engine) as session:
            self.assertEqual(session.get(PremiumContactEmail, state["email_id"]).role, "recruiter")
        engine.dispose()

        command.downgrade(config, "20260909_0047")
        downgraded = sa.create_engine(settings.database_url)
        self.addCleanup(downgraded.dispose)
        self.assertNotIn(
            "role", {c["name"] for c in sa.inspect(downgraded).get_columns("premium_contact_emails")}
        )

    def test_backfills_employer_role_only_when_unambiguous(self) -> None:
        state = {}

        def seed(session: Session) -> None:
            # Employer-side address: matches employer_email, not recruiter_email.
            employer_side = _contact(
                owner_name="Bob", company="Globex", is_employer=True,
                recruiter_email="recruiter@globex.com", employer_email="hiring@globex.com",
            )
            # Same address in BOTH columns - ambiguous, must stay "recruiter" (status quo).
            both = _contact(
                recruiter_name="Cid", owner_name="Cid", company="Initech",
                is_recruiter=True, is_employer=True,
                recruiter_email="cid@initech.com", employer_email="cid@initech.com",
            )
            session.add_all([employer_side, both])
            session.flush()
            rows = [
                PremiumContactEmail(
                    owner_id=OWNER_ID, premium_contact_id=employer_side.id,
                    normalized_email="hiring@globex.com", domain="globex.com",
                ),
                PremiumContactEmail(
                    owner_id=OWNER_ID, premium_contact_id=both.id,
                    normalized_email="cid@initech.com", domain="initech.com",
                ),
            ]
            session.add_all(rows)
            session.flush()
            state["employer_email_id"] = rows[0].id
            state["ambiguous_email_id"] = rows[1].id

        engine, _, _ = self._run(seed)
        with Session(engine) as session:
            self.assertEqual(session.get(PremiumContactEmail, state["employer_email_id"]).role, "employer")
            self.assertEqual(session.get(PremiumContactEmail, state["ambiguous_email_id"]).role, "recruiter")

    def test_adopts_headline_email_that_has_no_child_row(self) -> None:
        def seed(session: Session) -> None:
            session.add(
                _contact(
                    recruiter_name="Dana", company="Umbrella", is_recruiter=True,
                    recruiter_email="Dana@Umbrella.com", recruiter_email_domain="umbrella.com",
                )
            )

        engine, _, _ = self._run(seed)
        with Session(engine) as session:
            adopted = session.query(PremiumContactEmail).all()
            self.assertEqual(len(adopted), 1)
            # Lowercased on the way in, and marked primary + recruiter-side.
            self.assertEqual(adopted[0].normalized_email, "dana@umbrella.com")
            self.assertEqual(adopted[0].domain, "umbrella.com")
            self.assertEqual(adopted[0].role, "recruiter")
            self.assertTrue(adopted[0].is_primary)

    def test_reports_mismatch_without_touching_either_side(self) -> None:
        """The temp146 case: contact A's name and domain both say it owns the address,
        but the child table says contact B does. The migration must not pick a winner."""
        state = {}

        def seed(session: Session) -> None:
            headline_holder = _contact(
                recruiter_name="Harshitha Voddepally", company="Horizons of Tech", is_recruiter=True,
                recruiter_email="harshitha@horizonsoftech.net",
                recruiter_email_domain="horizonsoftech.net",
            )
            child_holder = _contact(
                recruiter_name="Leo", company="Nascent Technologies Inc", is_recruiter=True,
                recruiter_email="leo@nascent.com",
            )
            session.add_all([headline_holder, child_holder])
            session.flush()
            session.add_all([
                PremiumContactEmail(
                    owner_id=OWNER_ID, premium_contact_id=child_holder.id,
                    normalized_email="harshitha@horizonsoftech.net", domain="horizonsoftech.net",
                ),
                PremiumContactEmail(
                    owner_id=OWNER_ID, premium_contact_id=child_holder.id,
                    normalized_email="leo@nascent.com", domain="nascent.com",
                ),
            ])
            state["headline_id"] = headline_holder.id
            state["child_id"] = child_holder.id

        engine, _, _ = self._run(seed)
        with Session(engine) as session:
            headline = session.get(PremiumNumberContact, state["headline_id"])
            # Untouched: the whole point of report-only.
            self.assertEqual(headline.recruiter_email, "harshitha@horizonsoftech.net")
            self.assertEqual(headline.recruiter_email_domain, "horizonsoftech.net")
            # And no child row was stolen from or handed to anyone.
            owners = {
                row.normalized_email: row.premium_contact_id
                for row in session.query(PremiumContactEmail).all()
            }
            self.assertEqual(owners["harshitha@horizonsoftech.net"], state["child_id"])

            reports = session.query(ContactIdentityAction).filter(
                ContactIdentityAction.action_type == "identity_mismatch_report"
            ).all()
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].primary_contact_id, state["headline_id"])
            self.assertEqual(reports[0].secondary_contact_id, state["child_id"])
            payload = json.loads(reports[0].value)
            self.assertEqual(payload["email"], "harshitha@horizonsoftech.net")
            self.assertEqual(payload["headline_column"], "recruiter_email")
            self.assertEqual(payload["child_owner_contact_id"], state["child_id"])

    def test_two_contacts_claiming_one_unadopted_email_adopt_once_and_report_once(self) -> None:
        state = {}

        def seed(session: Session) -> None:
            first = _contact(recruiter_name="Eve", company="Hooli", is_recruiter=True,
                             recruiter_email="shared@hooli.com")
            second = _contact(recruiter_name="Finn", company="Hooli", is_recruiter=True,
                              recruiter_email="shared@hooli.com")
            session.add_all([first, second])
            session.flush()
            state["first_id"], state["second_id"] = first.id, second.id

        engine, _, _ = self._run(seed)
        with Session(engine) as session:
            rows = session.query(PremiumContactEmail).all()
            self.assertEqual(len(rows), 1)
            # Lowest id adopts; the unique constraint permits exactly one owner.
            self.assertEqual(rows[0].premium_contact_id, state["first_id"])
            reports = session.query(ContactIdentityAction).filter(
                ContactIdentityAction.action_type == "identity_mismatch_report"
            ).all()
            self.assertEqual([r.primary_contact_id for r in reports], [state["second_id"]])

    def test_reports_soft_deleted_contact_still_holding_a_phone_slot(self) -> None:
        state = {}

        def seed(session: Session) -> None:
            from datetime import UTC, datetime

            stale = _contact(
                recruiter_name="Gus", company="Pied Piper", is_recruiter=True,
                normalized_phone_number="14155551111", phone_extension="",
                deleted_at=datetime.now(UTC),
            )
            session.add(stale)
            session.flush()
            state["stale_id"] = stale.id

        engine, _, _ = self._run(seed)
        with Session(engine) as session:
            reports = session.query(ContactIdentityAction).filter(
                ContactIdentityAction.action_type == "stale_phone_claim_report"
            ).all()
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0].primary_contact_id, state["stale_id"])
            self.assertEqual(json.loads(reports[0].value)["normalized_phone_number"], "14155551111")
            # Still soft-deleted, still holding the number - reported, not repaired.
            self.assertEqual(
                session.get(PremiumNumberContact, state["stale_id"]).normalized_phone_number,
                "14155551111",
            )


if __name__ == "__main__":
    unittest.main()
