"""G3 / §13: asking to be deleted, and the two gates on the request.

*"Requires re-authentication and a typed confirmation — a live session is not
authority to destroy an account, and neither is a borrowed laptop."*

The gates guard different things and both tests below matter. The **typed
confirmation** is against acting on the wrong account. The **re-authentication
window** is against someone acting on an account that is not theirs at all.

Under choice A the request itself destroys nothing beyond what deactivation
already destroys; the rows go when the recovery window closes.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database, main
from app.config import settings
from app.db import Base
from app.models import User
from app.routers import account as account_router
from app.services import account_service

EMAIL = "owner@example.com"
OWNER = "usr_leaving"


class DeletionRequestTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        main.app.dependency_overrides[main.get_db] = self._session
        # `resolve_owner_from_session` opens its own session through
        # `SessionLocal`, not through the dependency override, so without this
        # the middleware queries the ambient database, fails to find `users`
        # and 401s before the route is ever reached.
        self._previous_session_local = (database.SessionLocal, main.SessionLocal)
        database.SessionLocal = self.Session
        main.SessionLocal = self.Session
        self.client = TestClient(main.app)
        self.now = datetime.now(UTC)

        db = self.Session()
        db.add(User(email=EMAIL, owner_id=OWNER, last_login_at=self.now))
        db.commit()
        db.close()

    def _session(self):
        db = self.Session()
        try:
            yield db
        finally:
            db.close()

    def tearDown(self):
        database.SessionLocal, main.SessionLocal = self._previous_session_local
        main.app.dependency_overrides.pop(main.get_db, None)
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _set_last_login(self, when):
        db = self.Session()
        db.query(User).filter(User.owner_id == OWNER).one().last_login_at = when
        db.commit()
        db.close()

    def _delete(self, confirm_email=EMAIL):
        # Resolved from the request's own session, as the real resolver does.
        # Returning an object bound to another session makes the route mutate
        # one and commit another, and it answers 200 having written nothing.
        def resolve(request_db, token):
            return request_db.query(User).filter(User.owner_id == OWNER).first()

        self.client.cookies.set(settings.session_cookie_name, "a-session-token")
        try:
            with patch.object(settings, "feature_auth_enabled", True), \
                    patch.object(main.auth_service, "resolve_session", resolve), \
                    patch.object(account_service, "revoke_google_token", lambda token: None):
                return self.client.request(
                    "DELETE", "/account", json={"confirm_email": confirm_email}
                )
        finally:
            self.client.cookies.clear()

    def _user(self) -> User:
        db = self.Session()
        try:
            return db.query(User).filter(User.owner_id == OWNER).one()
        finally:
            db.close()

    # --- the typed confirmation ------------------------------------------

    def test_the_right_address_is_accepted(self):
        response = self._delete()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("purge_after", response.json())

    def test_a_different_address_is_refused(self):
        """Against acting on the wrong account - the case that matters when
        someone is signed in to two of them."""
        response = self._delete(confirm_email="someone.else@example.com")
        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self._user().deletion_requested_at, "nothing may be scheduled")

    def test_the_comparison_ignores_case_and_padding(self):
        self.assertEqual(self._delete(confirm_email=f"  {EMAIL.upper()} ").status_code, 200)

    def test_an_empty_confirmation_is_refused(self):
        self.assertEqual(self._delete(confirm_email="").status_code, 400)

    # --- the re-authentication window ------------------------------------

    def test_a_stale_session_must_sign_in_again(self):
        """`last_login_at` moves only on a completed Google sign-in, so a
        stolen cookie cannot refresh it. That is the borrowed laptop."""
        self._set_last_login(self.now - account_router.REAUTH_WINDOW - timedelta(minutes=1))
        response = self._delete()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "reauthentication_required")
        self.assertIsNone(self._user().deletion_requested_at)

    def test_the_window_is_actually_short(self):
        """Pins the value, not just the mechanism.

        Every other test here derives its timing *from* `REAUTH_WINDOW`, so
        they scale with it: widen the constant to ten years and they all stay
        green while the gate stops gating anything. §13 wants proof the person
        is still there, and an hour is already generous for that.
        """
        self.assertLessEqual(account_router.REAUTH_WINDOW, timedelta(hours=1))
        self.assertGreaterEqual(account_router.REAUTH_WINDOW, timedelta(minutes=1))

    def test_an_account_that_never_signed_in_cannot_delete_itself(self):
        self._set_last_login(None)
        self.assertEqual(self._delete().status_code, 401)

    def test_a_fresh_sign_in_is_inside_the_window(self):
        self._set_last_login(self.now - timedelta(minutes=1))
        self.assertEqual(self._delete().status_code, 200)

    # --- what the request actually does ----------------------------------

    def test_it_schedules_rather_than_destroys(self):
        """Choice A: the 30-day window is the product. The rows are still here
        when the request returns, and an administrator can still restore them."""
        self._delete()
        user = self._user()
        self.assertIsNotNone(user.deletion_requested_at)
        self.assertIsNotNone(user.disabled_at)
        self.assertIsNotNone(user, "the account row must still exist")

    def test_the_purge_date_is_the_recovery_window_away(self):
        body = self._delete().json()
        deactivated = datetime.fromisoformat(body["deactivated_at"].replace("Z", "+00:00"))
        purge_after = datetime.fromisoformat(body["purge_after"].replace("Z", "+00:00"))
        self.assertEqual(
            round((purge_after - deactivated).total_seconds() / 86400),
            account_service.RECOVERY_DAYS,
        )

    def test_the_session_cookie_is_cleared(self):
        response = self._delete()
        self.assertIn("set-cookie", {key.lower() for key in response.headers})


if __name__ == "__main__":
    unittest.main()
