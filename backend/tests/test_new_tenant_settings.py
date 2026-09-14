"""A tenant that has never been seeded must still be able to read settings.

Found in live use, one layer behind the missing-cookie bug: once the second
user's requests actually reached the API with a session, `/settings/bootstrap`
and `/ai/status` returned 500 "Settings not initialized" and the dashboard
showed "Failed to load saved settings".

`ensure_default_settings()` is called once, from `startup_service.startup()`,
where the owner in scope is still the fallback constant. So exactly one owner
ever got a `user_settings` row, and every user after the first had none.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tenancy
from app.models import Base, UserSettings
from app.services.settings_bootstrap_service import SettingsBootstrapService


class NewTenantSettingsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.service = SettingsBootstrapService(session_factory=self.Session)

    def tearDown(self):
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _rows(self, db) -> list[str]:
        return [r.owner_id for r in db.query(UserSettings).all()]

    def test_a_never_seeded_tenant_is_seeded_on_first_read(self):
        reset = tenancy.set_owner_id("usr_second_user")
        try:
            db = self.Session()
            self.assertEqual(self._rows(db), [], "precondition: no settings anywhere")
            resolved = self.service.get_settings(db)
            self.assertEqual(resolved.owner_id, "usr_second_user")
            self.assertEqual(self._rows(db), ["usr_second_user"])
            db.close()
        finally:
            tenancy.reset_owner_id(reset)

    def test_seeding_one_tenant_does_not_create_rows_for_another(self):
        first = tenancy.set_owner_id("usr_one")
        db = self.Session()
        self.service.get_settings(db)
        tenancy.reset_owner_id(first)

        second = tenancy.set_owner_id("usr_two")
        self.service.get_settings(db)
        tenancy.reset_owner_id(second)

        self.assertEqual(sorted(self._rows(db)), ["usr_one", "usr_two"])
        db.close()

    def test_a_second_read_reuses_the_row_rather_than_adding_one(self):
        reset = tenancy.set_owner_id("usr_repeat")
        try:
            db = self.Session()
            first = self.service.get_settings(db)
            second = self.service.get_settings(db)
            self.assertEqual(first.id, second.id)
            self.assertEqual(self._rows(db), ["usr_repeat"])
            db.close()
        finally:
            tenancy.reset_owner_id(reset)

    def test_an_edit_survives_the_next_read(self):
        # Seeding must never overwrite a tenant that has configured anything.
        reset = tenancy.set_owner_id("usr_edited")
        try:
            db = self.Session()
            seeded = self.service.get_settings(db)
            seeded.gmail_query = "is:unread from:recruiter"
            db.commit()

            again = self.service.get_settings(db)
            self.assertEqual(again.gmail_query, "is:unread from:recruiter")
            self.assertEqual(self._rows(db), ["usr_edited"])
            db.close()
        finally:
            tenancy.reset_owner_id(reset)

    def test_losing_the_insert_race_still_returns_a_row(self):
        """Two first requests from one new tenant collide on a unique owner_id."""
        reset = tenancy.set_owner_id("usr_racer")
        try:
            other = self.Session()
            db = self.Session()

            original = self.service.ensure_default_settings

            def seed_twice():
                # Stand in for the concurrent request that committed first.
                original()
                original()

            self.service.ensure_default_settings = seed_twice
            resolved = self.service.get_settings(db)
            self.assertEqual(resolved.owner_id, "usr_racer")
            self.assertEqual(self._rows(db), ["usr_racer"])
            other.close()
            db.close()
        finally:
            tenancy.reset_owner_id(reset)


if __name__ == "__main__":
    unittest.main()
