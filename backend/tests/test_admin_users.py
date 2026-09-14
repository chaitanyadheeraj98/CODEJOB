"""B6 — the admin view of accounts.

Two rules under test, both from temp176 §3:

  2. `is_admin` is never settable through a request body and never read from a
     token claim - only the database column, checked server-side.
  4. No user content on an admin page. Identifiers, state and timings only.
"""

import os
import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database, main
from app.config import settings
from app.db import Base
from app.models import RecruiterEmail, User, UserSession
from app.services import auth_service
from app.services.google_identity_service import VerifiedIdentity

ADMIN = VerifiedIdentity(subject="sub-admin", email="admin@example.com", email_verified=True, name="Admin")
MEMBER = VerifiedIdentity(subject="sub-member", email="member@example.com", email_verified=True, name="Member")


class AdminUsersTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app, follow_redirects=False)
        self._previous = (
            settings.feature_auth_enabled,
            settings.google_client_id,
            settings.google_client_secret,
            settings.session_cookie_secure,
            database.SessionLocal,
            main.SessionLocal,
        )
        settings.feature_auth_enabled = True
        settings.google_client_id = "client-id"
        settings.google_client_secret = "client-secret"
        settings.session_cookie_secure = False
        database.SessionLocal = self.SessionLocal
        main.SessionLocal = self.SessionLocal

    def tearDown(self) -> None:
        (
            settings.feature_auth_enabled,
            settings.google_client_id,
            settings.google_client_secret,
            settings.session_cookie_secure,
            database.SessionLocal,
            main.SessionLocal,
        ) = self._previous
        main.app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def _sign_in(self, identity: VerifiedIdentity) -> str:
        self.client.cookies.clear()
        self.client.cookies.set(auth_service.STATE_COOKIE, "state")
        with (
            patch.object(auth_service, "exchange_code", return_value=Mock(id_token="raw")),
            patch("app.services.google_identity_service.verify_id_token", return_value=identity),
            patch("app.gmail_client._profile_email", return_value=identity.email),
            patch("app.gmail_client.store_credentials_for_owner"),
        ):
            response = self.client.get("/auth/google/callback", params={"code": "c", "state": "state"})
        return response.cookies[settings.session_cookie_name]

    def _as(self, token: str) -> None:
        self.client.cookies.clear()
        self.client.cookies.set(settings.session_cookie_name, token)

    def _promote(self, identity: VerifiedIdentity) -> None:
        with Session(self.engine) as db:
            db.query(User).filter(User.google_subject == identity.subject).one().is_admin = True
            db.commit()

    def _both(self) -> tuple[str, str]:
        admin_token = self._sign_in(ADMIN)
        member_token = self._sign_in(MEMBER)
        self._promote(ADMIN)
        return admin_token, member_token

    def _member_id(self) -> int:
        with Session(self.engine) as db:
            return db.query(User).filter(User.google_subject == MEMBER.subject).one().id

    # -- access ----------------------------------------------------------

    def test_an_admin_can_list_accounts(self) -> None:
        admin_token, _ = self._both()
        self._as(admin_token)

        body = self.client.get("/admin/users").json()

        self.assertEqual({row["email"] for row in body}, {"admin@example.com", "member@example.com"})

    def test_a_non_admin_is_refused(self) -> None:
        _, member_token = self._both()
        self._as(member_token)

        self.assertEqual(self.client.get("/admin/users").status_code, 403)

    def test_an_anonymous_caller_is_refused(self) -> None:
        self._both()
        self.client.cookies.clear()

        self.assertEqual(self.client.get("/admin/users").status_code, 401)

    def test_the_routes_are_absent_when_sign_in_is_off(self) -> None:
        admin_token, _ = self._both()
        self._as(admin_token)
        settings.feature_auth_enabled = False

        self.assertEqual(self.client.get("/admin/users").status_code, 404)

    # -- rule 2: is_admin is not client-settable -------------------------

    def test_a_member_cannot_promote_themselves(self) -> None:
        _, member_token = self._both()
        member_id = self._member_id()
        self._as(member_token)

        response = self.client.patch(f"/admin/users/{member_id}", json={"is_admin": True})

        self.assertEqual(response.status_code, 403)
        with Session(self.engine) as db:
            self.assertFalse(db.query(User).filter(User.id == member_id).one().is_admin)

    def test_signing_in_never_grants_admin(self) -> None:
        """Nothing in the identity or the request body can set it."""
        self._sign_in(MEMBER)

        with Session(self.engine) as db:
            self.assertFalse(db.query(User).filter(User.google_subject == MEMBER.subject).one().is_admin)

    def test_an_admin_can_promote_someone_else(self) -> None:
        admin_token, _ = self._both()
        member_id = self._member_id()
        self._as(admin_token)

        body = self.client.patch(f"/admin/users/{member_id}", json={"is_admin": True}).json()

        self.assertTrue(body["is_admin"])

    def test_an_admin_cannot_demote_themselves(self) -> None:
        """Locking the last admin out is a support call nobody can answer."""
        admin_token, _ = self._both()
        with Session(self.engine) as db:
            admin_id = db.query(User).filter(User.google_subject == ADMIN.subject).one().id
        self._as(admin_token)

        response = self.client.patch(f"/admin/users/{admin_id}", json={"is_admin": False})

        self.assertEqual(response.status_code, 422)

    # -- disabling -------------------------------------------------------

    def test_disabling_ends_live_sessions_not_just_future_sign_ins(self) -> None:
        admin_token, member_token = self._both()
        member_id = self._member_id()
        self._as(admin_token)

        self.client.patch(f"/admin/users/{member_id}", json={"disabled": True})

        with Session(self.engine) as db:
            sessions = db.query(UserSession).filter(UserSession.user_id == member_id).all()
            self.assertTrue(all(row.revoked_at is not None for row in sessions))
        self._as(member_token)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_re_enabling_clears_the_flag(self) -> None:
        admin_token, _ = self._both()
        member_id = self._member_id()
        self._as(admin_token)
        self.client.patch(f"/admin/users/{member_id}", json={"disabled": True})
        with Session(self.engine) as db:
            db.query(User).filter(User.id == member_id).one().deletion_requested_at = datetime.now(UTC)
            db.commit()

        body = self.client.patch(f"/admin/users/{member_id}", json={"disabled": False}).json()

        self.assertFalse(body["disabled"])
        with Session(self.engine) as db:
            self.assertIsNone(db.query(User).filter(User.id == member_id).one().deletion_requested_at)

    def test_an_admin_cannot_disable_themselves(self) -> None:
        admin_token, _ = self._both()
        with Session(self.engine) as db:
            admin_id = db.query(User).filter(User.google_subject == ADMIN.subject).one().id
        self._as(admin_token)

        self.assertEqual(
            self.client.patch(f"/admin/users/{admin_id}", json={"disabled": True}).status_code, 422
        )

    def test_an_unknown_user_is_404(self) -> None:
        admin_token, _ = self._both()
        self._as(admin_token)

        self.assertEqual(self.client.patch("/admin/users/9999", json={"disabled": True}).status_code, 404)

    # -- rule 4: no user content -----------------------------------------

    def test_the_listing_carries_no_mail_and_no_token_material(self) -> None:
        admin_token, _ = self._both()
        with Session(self.engine) as db:
            member = db.query(User).filter(User.google_subject == MEMBER.subject).one()
            db.add(
                RecruiterEmail(
                    owner_id=member.owner_id,
                    sender="recruiter@example.com",
                    subject="SENSITIVE-SUBJECT-LINE",
                    body="SENSITIVE-BODY-TEXT",
                    role="",
                    state="needs_review",
                )
            )
            db.commit()
        self._as(admin_token)

        raw = self.client.get("/admin/users").text

        self.assertNotIn("SENSITIVE-SUBJECT-LINE", raw)
        self.assertNotIn("SENSITIVE-BODY-TEXT", raw)
        for word in ("token", "refresh", "secret", "password"):
            self.assertNotIn(word, raw.lower())

    def test_the_listing_reports_connection_state_without_the_credential(self) -> None:
        admin_token, _ = self._both()
        self._as(admin_token)

        row = next(r for r in self.client.get("/admin/users").json() if r["email"] == "member@example.com")

        self.assertIn("gmail_connected", row)
        self.assertFalse(row["gmail_connected"])


if __name__ == "__main__":
    unittest.main()
