"""B4 — resolving whose data a request or job is touching.

`settings.owner_id` is read in 421 places. This bridge is what lets sign-in
land before those are swept: the value is resolved once, here, and everything
still reading the constant keeps working exactly as it did.

The sharpest edge is the background worker. A job has no request and therefore
no middleware; one that forgets to set the owner writes one user's data into
another's account and raises nothing at all.
"""

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from unittest.mock import Mock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database, main, tenancy
from app.config import settings
from app.db import Base
from app.models import User
from app.services import auth_service
from app.services.google_identity_service import VerifiedIdentity

IDENTITY = VerifiedIdentity(subject="sub-1", email="owner@example.com", email_verified=True, name="Owner")


class OwnerContextTests(unittest.TestCase):
    def test_it_falls_back_to_the_configured_owner(self) -> None:
        """What keeps single-tenant behaviour identical while the flag is off."""
        self.assertEqual(tenancy.owner_id(), settings.owner_id)
        self.assertIsNone(tenancy.current_owner_id_or_none())

    def test_a_scope_sets_and_restores_the_owner(self) -> None:
        with tenancy.owner_scope("usr_a") as owner:
            self.assertEqual(owner, "usr_a")
            self.assertEqual(tenancy.owner_id(), "usr_a")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_a_scope_restores_even_when_the_body_raises(self) -> None:
        """A job that blows up must not leak its owner into the next one."""
        with self.assertRaises(RuntimeError):
            with tenancy.owner_scope("usr_a"):
                raise RuntimeError("job failed")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_scopes_nest(self) -> None:
        with tenancy.owner_scope("usr_a"):
            with tenancy.owner_scope("usr_b"):
                self.assertEqual(tenancy.owner_id(), "usr_b")
            self.assertEqual(tenancy.owner_id(), "usr_a")

    def test_setting_none_falls_back_rather_than_returning_none(self) -> None:
        with tenancy.owner_scope(None):
            self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_threads_do_not_share_an_owner(self) -> None:
        """FastAPI runs sync endpoints in a threadpool; a shared owner would be a leak."""

        def run(owner: str) -> str:
            with tenancy.owner_scope(owner):
                return tenancy.owner_id()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ["usr_a", "usr_b"]))

        self.assertEqual(results, ["usr_a", "usr_b"])
        self.assertEqual(tenancy.owner_id(), settings.owner_id)


class MiddlewareTests(unittest.TestCase):
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

    def _sign_in(self) -> str:
        self.client.cookies.set(auth_service.STATE_COOKIE, "the-state")
        with (
            patch.object(auth_service, "exchange_code", return_value=Mock(id_token="raw")),
            patch("app.services.google_identity_service.verify_id_token", return_value=IDENTITY),
            patch("app.gmail_client._profile_email", return_value=IDENTITY.email),
            patch("app.gmail_client.store_credentials_for_owner"),
        ):
            response = self.client.get(
                "/auth/google/callback", params={"code": "c", "state": "the-state"}
            )
        token = response.cookies[settings.session_cookie_name]
        self.client.cookies.set(settings.session_cookie_name, token)
        return token

    def test_a_signed_in_request_carries_that_users_owner(self) -> None:
        """Observed *during* the request, via a dependency.

        Dependencies resolve after the middleware has set the ContextVar and
        before it resets, so this sees the request-scoped value rather than the
        fallback. Reading it after the response has returned would only ever
        see the fallback and would prove nothing.
        """
        self._sign_in()
        with Session(self.engine) as db:
            expected = db.query(User).one().owner_id
        seen: list[str] = []

        def capture_owner():
            seen.append(tenancy.owner_id())
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = capture_owner
        self.client.get("/auth/me")

        self.assertEqual(seen, [expected])
        self.assertNotEqual(expected, settings.owner_id, "must not be the fallback")

    def test_an_unauthenticated_request_leaves_the_fallback_in_place(self) -> None:
        self.client.get("/auth/me")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_the_owner_does_not_leak_past_the_request(self) -> None:
        self._sign_in()

        self.client.get("/auth/me")

        self.assertIsNone(tenancy.current_owner_id_or_none())

    def test_a_client_supplied_owner_id_is_ignored(self) -> None:
        """Rule 1. An owner_id-shaped parameter must never influence anything."""
        self.client.cookies.clear()

        for params in ({"owner_id": "usr_someone_else"}, {"ownerId": "usr_someone_else"}):
            with self.subTest(params=params):
                self.client.get("/auth/me", params=params)
                self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_a_disabled_user_falls_back_rather_than_keeping_their_owner(self) -> None:
        self._sign_in()
        with Session(self.engine) as db:
            db.query(User).one().disabled_at = datetime.now(UTC)
            db.commit()

        self.assertEqual(self.client.get("/auth/me").status_code, 401)
        self.assertEqual(tenancy.owner_id(), settings.owner_id)

    def test_the_middleware_is_inert_while_the_feature_is_off(self) -> None:
        settings.feature_auth_enabled = False

        self.client.get("/gmail/status")

        self.assertEqual(tenancy.owner_id(), settings.owner_id)


if __name__ == "__main__":
    unittest.main()
