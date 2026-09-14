"""B7 — the first sign-in inherits the existing single-tenant install.

Without this, turning sign-in on would show the owner an empty application:
every row in the database belongs to `settings.owner_id`, and a new sign-in
would be given a freshly generated one.

The chosen fix is adoption, not migration. Rewriting `owner_id` across 84
columns to point at a new id is a long, irreversible data migration with a
partial-failure mode, run against a live database, to achieve exactly what
reusing the existing string achieves for nothing.

Because it hands over an entire install, the guards are what these tests are
really about.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import User
from app.services import auth_service
from app.services.google_identity_service import VerifiedIdentity

OWNER = VerifiedIdentity(subject="sub-owner", email="owner@example.com", email_verified=True, name="Owner")
OTHER = VerifiedIdentity(subject="sub-other", email="other@example.com", email_verified=True, name="Other")


class BootstrapOwnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._previous = settings.bootstrap_owner_email
        settings.bootstrap_owner_email = "owner@example.com"

    def tearDown(self) -> None:
        settings.bootstrap_owner_email = self._previous
        self.db.close()
        self.engine.dispose()

    # -- the claim -------------------------------------------------------

    def test_the_configured_address_adopts_the_existing_owner_id(self) -> None:
        """No data is moved; the new account simply *is* the existing owner."""
        user = auth_service.upsert_user(self.db, OWNER)

        self.assertEqual(user.owner_id, settings.owner_id)

    def test_the_configured_address_becomes_an_admin(self) -> None:
        self.assertTrue(auth_service.upsert_user(self.db, OWNER).is_admin)

    # -- the guards ------------------------------------------------------

    def test_anybody_else_gets_a_generated_owner_and_no_admin(self) -> None:
        user = auth_service.upsert_user(self.db, OTHER)

        self.assertTrue(user.owner_id.startswith("usr_"))
        self.assertNotEqual(user.owner_id, settings.owner_id)
        self.assertFalse(user.is_admin)

    def test_the_install_can_only_be_inherited_once(self) -> None:
        """Even if the setting is later pointed at somebody else."""
        first = auth_service.upsert_user(self.db, OWNER)
        self.assertEqual(first.owner_id, settings.owner_id)

        settings.bootstrap_owner_email = "second@example.com"
        second = auth_service.upsert_user(
            self.db,
            VerifiedIdentity(subject="sub-second", email="second@example.com", email_verified=True, name="Second"),
        )

        self.assertNotEqual(second.owner_id, settings.owner_id)
        self.assertTrue(second.owner_id.startswith("usr_"))
        self.assertFalse(second.is_admin, "a second claimant gets no special treatment")

    def test_an_empty_setting_disables_the_behaviour_entirely(self) -> None:
        settings.bootstrap_owner_email = ""

        user = auth_service.upsert_user(self.db, OWNER)

        self.assertTrue(user.owner_id.startswith("usr_"))
        self.assertFalse(user.is_admin)

    def test_the_match_ignores_case_and_whitespace(self) -> None:
        settings.bootstrap_owner_email = "  Owner@Example.COM "

        self.assertEqual(auth_service.upsert_user(self.db, OWNER).owner_id, settings.owner_id)

    def test_a_near_miss_address_does_not_claim_the_install(self) -> None:
        for address in ("owner@example.com.evil.test", "notowner@example.com", "owner@example.co"):
            with self.subTest(address=address):
                identity = VerifiedIdentity(
                    subject=f"sub-{address}", email=address, email_verified=True, name="X"
                )
                user = auth_service.upsert_user(self.db, identity)
                self.assertNotEqual(user.owner_id, settings.owner_id)
                self.assertFalse(user.is_admin)

    def test_signing_in_again_does_not_re_grant_admin(self) -> None:
        """An admin removed through /admin/users must stay removed."""
        auth_service.upsert_user(self.db, OWNER)
        self.db.query(User).one().is_admin = False
        self.db.flush()

        auth_service.upsert_user(self.db, OWNER)

        self.assertFalse(self.db.query(User).one().is_admin)

    def test_the_existing_owners_data_is_reachable_without_being_rewritten(self) -> None:
        """The whole point: adoption rather than an 84-column migration."""
        from app.models import RecruiterEmail

        self.db.add(
            RecruiterEmail(
                owner_id=settings.owner_id,
                sender="recruiter@example.com",
                subject="EXISTING-ROW",
                body="b",
                role="",
                state="needs_review",
            )
        )
        self.db.flush()

        user = auth_service.upsert_user(self.db, OWNER)

        rows = self.db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == user.owner_id).all()
        self.assertEqual([row.subject for row in rows], ["EXISTING-ROW"])

    def test_the_existing_gmail_credential_comes_with_it(self) -> None:
        """The install's mailbox connection is keyed on the same owner_id."""
        from app.models import GmailCredential

        self.db.add(GmailCredential(owner_id=settings.owner_id, google_email="owner@example.com"))
        self.db.flush()

        user = auth_service.upsert_user(self.db, OWNER)

        row = self.db.query(GmailCredential).filter(GmailCredential.owner_id == user.owner_id).one()
        self.assertEqual(row.google_email, "owner@example.com")


if __name__ == "__main__":
    unittest.main()
