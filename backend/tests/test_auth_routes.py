"""B3 — sign-in: sessions, cookies, and the /auth routes.

The rules being enforced, in the order they matter:

  1. `owner_id` comes from a verified session and nowhere else.
  2. The OAuth `state` must match, or the callback was not started by this
     browser.
  3. A session token is stored hashed, so a leaked database yields none.
  4. With the feature off, every route here is absent.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import User, UserSession
from app.services import auth_service
from app.services.google_identity_service import VerifiedIdentity

IDENTITY = VerifiedIdentity(subject="sub-1", email="owner@example.com", email_verified=True, name="Owner")
OTHER = VerifiedIdentity(subject="sub-2", email="second@example.com", email_verified=True, name="Second")


class AuthTestBase(unittest.TestCase):
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
            main.settings.feature_auth_enabled,
            main.settings.google_client_id,
            main.settings.google_client_secret,
            main.settings.session_cookie_secure,
        )
        main.settings.feature_auth_enabled = True
        main.settings.google_client_id = "client-id"
        main.settings.google_client_secret = "client-secret"
        # TestClient speaks http; a Secure cookie would never come back.
        main.settings.session_cookie_secure = False

    def tearDown(self) -> None:
        (
            main.settings.feature_auth_enabled,
            main.settings.google_client_id,
            main.settings.google_client_secret,
            main.settings.session_cookie_secure,
        ) = self._previous
        main.app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def _sign_in(self, identity: VerifiedIdentity = IDENTITY, mailbox: str | None = None) -> None:
        """Drive the callback with Google stubbed out."""
        self.client.cookies.set(auth_service.STATE_COOKIE, "the-state")
        credentials = Mock(id_token="raw-id-token")
        with (
            patch.object(auth_service, "exchange_code", return_value=credentials),
            patch("app.services.google_identity_service.verify_id_token", return_value=identity),
            patch("app.gmail_client._profile_email", return_value=mailbox or identity.email),
            patch("app.gmail_client.store_credentials_for_owner") as store,
        ):
            self.response = self.client.get("/auth/google/callback", params={"code": "auth-code", "state": "the-state"})
        self.store = store


class FeatureFlagTests(AuthTestBase):
    def test_every_route_is_absent_when_sign_in_is_off(self) -> None:
        main.settings.feature_auth_enabled = False

        for method, path in (("get", "/auth/google/start"), ("get", "/auth/me"), ("post", "/auth/logout")):
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path).status_code, 404)


class LoginStartTests(AuthTestBase):
    def test_start_returns_a_consent_url_and_sets_the_state_cookie(self) -> None:
        response = self.client.get("/auth/google/start")

        self.assertEqual(response.status_code, 200)
        self.assertIn("accounts.google.com", response.json()["authorization_url"])
        self.assertIn(auth_service.STATE_COOKIE, response.cookies)

    def test_the_consent_asks_for_offline_access_and_a_fresh_refresh_token(self) -> None:
        """Without prompt=consent Google omits the refresh token after the first sign-in."""
        url = self.client.get("/auth/google/start").json()["authorization_url"]

        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)

    def test_start_is_unavailable_when_google_is_not_configured(self) -> None:
        main.settings.google_client_id = ""

        self.assertEqual(self.client.get("/auth/google/start").status_code, 503)


class CallbackTests(AuthTestBase):
    def test_a_successful_callback_creates_the_user_and_a_session(self) -> None:
        self._sign_in()

        self.assertEqual(self.response.status_code, 303)
        with Session(self.engine) as db:
            user = db.query(User).one()
            self.assertEqual(user.email, "owner@example.com")
            self.assertEqual(user.google_subject, "sub-1")
            self.assertTrue(user.owner_id.startswith("usr_"))
            self.assertEqual(db.query(UserSession).count(), 1)

    def test_the_owner_id_is_generated_not_derived_from_the_email(self) -> None:
        """It is stamped across 82 columns; an address there would leak identity."""
        self._sign_in()

        with Session(self.engine) as db:
            self.assertNotIn("owner@example.com", db.query(User).one().owner_id)

    def test_the_credential_is_stored_against_that_users_owner_id(self) -> None:
        self._sign_in()

        with Session(self.engine) as db:
            owner_id = db.query(User).one().owner_id
        self.assertEqual(self.store.call_args.args[0], owner_id)

    def test_only_the_session_hash_is_stored(self) -> None:
        self._sign_in()
        raw = self.response.cookies[main.settings.session_cookie_name]

        with Session(self.engine) as db:
            stored = db.query(UserSession).one().token_hash
        self.assertNotEqual(stored, raw)
        self.assertEqual(stored, auth_service.hash_session_token(raw))

    def test_a_mismatched_state_is_refused(self) -> None:
        """CSRF: an attacker cannot also set the cookie."""
        self.client.cookies.set(auth_service.STATE_COOKIE, "the-state")

        response = self.client.get("/auth/google/callback", params={"code": "c", "state": "different"})

        self.assertIn("login_error=state_mismatch", response.headers["location"])
        with Session(self.engine) as db:
            self.assertEqual(db.query(User).count(), 0)

    def test_a_callback_with_no_state_cookie_is_refused(self) -> None:
        response = self.client.get("/auth/google/callback", params={"code": "c", "state": "the-state"})

        self.assertIn("login_error=state_mismatch", response.headers["location"])

    def test_a_mailbox_that_is_not_the_signed_in_account_is_refused(self) -> None:
        self._sign_in(mailbox="someone.else@example.com")

        self.assertIn("login_error=identity_unverified", self.response.headers["location"])
        with Session(self.engine) as db:
            self.assertEqual(db.query(User).count(), 0)
        self.store.assert_not_called()

    def test_a_declined_consent_redirects_rather_than_erroring(self) -> None:
        response = self.client.get("/auth/google/callback", params={"error": "access_denied"})

        self.assertEqual(response.status_code, 303)
        self.assertIn("login_error=declined", response.headers["location"])

    def test_signing_in_twice_reuses_the_same_account(self) -> None:
        self._sign_in()
        self._sign_in()

        with Session(self.engine) as db:
            self.assertEqual(db.query(User).count(), 1)
            self.assertEqual(db.query(UserSession).count(), 2)

    def test_two_different_people_get_separate_owners(self) -> None:
        """The foundation of every cross-tenant guarantee that follows."""
        self._sign_in(IDENTITY)
        self.client.cookies.clear()
        self._sign_in(OTHER)

        with Session(self.engine) as db:
            owners = {user.owner_id for user in db.query(User)}
        self.assertEqual(len(owners), 2)

    def test_a_disabled_account_cannot_sign_in(self) -> None:
        self._sign_in()
        with Session(self.engine) as db:
            db.query(User).one().disabled_at = datetime.now(UTC)
            db.commit()
        self.client.cookies.clear()

        self._sign_in()

        self.assertIn("login_error=not_permitted", self.response.headers["location"])


class SessionTests(AuthTestBase):
    def _signed_in_client(self) -> str:
        self._sign_in()
        token = self.response.cookies[main.settings.session_cookie_name]
        self.client.cookies.set(main.settings.session_cookie_name, token)
        return token

    def test_me_reports_the_signed_in_user(self) -> None:
        self._signed_in_client()

        body = self.client.get("/auth/me").json()

        self.assertEqual(body["email"], "owner@example.com")
        self.assertFalse(body["is_admin"])

    def test_me_never_returns_token_material(self) -> None:
        self._signed_in_client()

        raw = self.client.get("/auth/me").text

        for word in ("token", "session", "secret"):
            self.assertNotIn(word, raw.lower())

    def test_me_is_401_without_a_cookie(self) -> None:
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_me_is_401_with_a_made_up_cookie(self) -> None:
        self.client.cookies.set(main.settings.session_cookie_name, "not-a-real-token")

        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_logout_revokes_the_session(self) -> None:
        self._signed_in_client()

        self.assertEqual(self.client.post("/auth/logout").status_code, 204)

        with Session(self.engine) as db:
            self.assertIsNotNone(db.query(UserSession).one().revoked_at)

    def test_a_revoked_session_stops_working(self) -> None:
        token = self._signed_in_client()
        self.client.post("/auth/logout")
        self.client.cookies.set(main.settings.session_cookie_name, token)

        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_an_expired_session_stops_working(self) -> None:
        self._signed_in_client()
        with Session(self.engine) as db:
            db.query(UserSession).one().expires_at = datetime.now(UTC) - timedelta(minutes=1)
            db.commit()

        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_disabling_an_account_invalidates_its_live_session(self) -> None:
        """Removing someone from the GCP list does not do this; the app must."""
        self._signed_in_client()
        with Session(self.engine) as db:
            db.query(User).one().disabled_at = datetime.now(UTC)
            db.commit()

        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_the_session_cookie_is_http_only_and_lax(self) -> None:
        self._sign_in()

        header = self.response.headers["set-cookie"]

        self.assertIn("httponly", header.lower())
        # Lax, not Strict: Strict would drop the cookie on Google's cross-site
        # redirect and break every sign-in.
        self.assertIn("samesite=lax", header.lower())

    def test_logging_out_everywhere_revokes_every_session(self) -> None:
        self._sign_in()
        self.client.cookies.clear()
        self._sign_in()

        with Session(self.engine) as db:
            user = db.query(User).one()
            self.assertEqual(auth_service.revoke_all_sessions(db, user.id), 2)
            db.commit()
            self.assertTrue(all(row.revoked_at is not None for row in db.query(UserSession)))


if __name__ == "__main__":
    unittest.main()


class PkceTests(AuthTestBase):
    """PKCE, and the bug that made the first live sign-in fail.

    `Flow.authorization_url()` mints a code verifier, hashes it into the
    `code_challenge` sent to Google, and keeps the verifier on that Flow
    object. The exchange happens in a different request against a new Flow, so
    unless the verifier travels with the state Google answers
    "invalid_grant: Missing code verifier" - which is exactly what the first
    real sign-in got.

    Every earlier auth test stubbed `exchange_code`, so none of them could see
    it. These do not stub the flow's own plumbing.
    """

    def test_start_sends_a_code_challenge_to_google(self) -> None:
        url = self.client.get("/auth/google/start").json()["authorization_url"]

        self.assertIn("code_challenge=", url)
        self.assertIn("code_challenge_method=S256", url)

    def test_start_keeps_the_verifier_in_an_http_only_cookie(self) -> None:
        response = self.client.get("/auth/google/start")

        self.assertIn(auth_service.VERIFIER_COOKIE, response.cookies)
        header = response.headers["set-cookie"]
        self.assertIn("httponly", header.lower())

    def test_the_verifier_is_the_one_that_produced_the_challenge(self) -> None:
        """The whole bug: a second Flow would mint a different verifier."""
        import base64
        import hashlib
        from urllib.parse import parse_qs, urlparse

        response = self.client.get("/auth/google/start")
        verifier = response.cookies[auth_service.VERIFIER_COOKIE]
        challenge = parse_qs(urlparse(response.json()["authorization_url"]).query)["code_challenge"][0]

        digest = hashlib.sha256(verifier.encode()).digest()
        expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")

        self.assertEqual(challenge, expected)

    def test_the_exchange_replays_the_stored_verifier(self) -> None:
        captured = {}

        def fake_fetch_token(self, **kwargs):
            # Read off the Flow itself: this is the value google-auth-oauthlib
            # would send to Google, and sending the wrong one is the bug.
            captured["verifier"] = self.code_verifier

        with (
            patch("google_auth_oauthlib.flow.Flow.fetch_token", fake_fetch_token),
            patch("google_auth_oauthlib.flow.Flow.credentials", new_callable=lambda: property(lambda self: Mock())),
        ):
            auth_service.exchange_code(code="c", state="s", code_verifier="the-verifier")

        self.assertEqual(captured["verifier"], "the-verifier")

    def test_the_callback_passes_the_cookie_through_to_the_exchange(self) -> None:
        self.client.cookies.set(auth_service.STATE_COOKIE, "the-state")
        self.client.cookies.set(auth_service.VERIFIER_COOKIE, "the-verifier")

        with (
            patch.object(auth_service, "exchange_code", return_value=Mock(id_token="raw")) as exchange,
            patch("app.services.google_identity_service.verify_id_token", return_value=IDENTITY),
            patch("app.gmail_client._profile_email", return_value=IDENTITY.email),
            patch("app.gmail_client.store_credentials_for_owner"),
        ):
            self.client.get("/auth/google/callback", params={"code": "c", "state": "the-state"})

        self.assertEqual(exchange.call_args.kwargs["code_verifier"], "the-verifier")

    def test_the_verifier_cookie_is_cleared_after_a_successful_sign_in(self) -> None:
        """Single use. A verifier left lying about outlives its own protection."""
        self._sign_in()

        header = self.response.headers["set-cookie"]
        self.assertIn(auth_service.VERIFIER_COOKIE, header)
        self.assertIn('Max-Age=0', header)
