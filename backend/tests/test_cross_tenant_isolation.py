"""§16 test 1 — one signed-in user must never see another's data.

This is the most important test in the multi-tenant work. Every other check
here protects a mechanism; this one protects the guarantee.

It is written **before** the `settings.owner_id` sweep (B5) deliberately, so it
first demonstrates the gap rather than asserting a fix that already exists: the
middleware resolves the right owner into the ContextVar while every service
still reads the configured constant, and the result is that a signed-in user
sees the constant owner's rows instead of their own.

Deliberately driven through the HTTP layer rather than by calling services. A
service called with an explicit `owner_id` will always look correct; what
matters is what a real signed-in request returns.
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
from app.models import RecruiterEmail, User, UserSettings
from app.services import auth_service
from app.services.google_identity_service import VerifiedIdentity

ALICE = VerifiedIdentity(subject="sub-alice", email="alice@example.com", email_verified=True, name="Alice")
BOB = VerifiedIdentity(subject="sub-bob", email="bob@example.com", email_verified=True, name="Bob")


class CrossTenantIsolationTests(unittest.TestCase):
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

    # -- helpers ---------------------------------------------------------

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
        token = response.cookies[settings.session_cookie_name]
        self.client.cookies.set(settings.session_cookie_name, token)
        return token

    def _owner_of(self, identity: VerifiedIdentity) -> str:
        with Session(self.engine) as db:
            return db.query(User).filter(User.google_subject == identity.subject).one().owner_id

    def _seed_candidate(self, owner_id: str, subject: str) -> None:
        with Session(self.engine) as db:
            db.add(UserSettings(owner_id=owner_id))
            db.add(
                RecruiterEmail(
                    owner_id=owner_id,
                    sender=f"recruiter@{subject.lower()}.test",
                    subject=subject,
                    body="body",
                    role="",
                    state="needs_review",
                )
            )
            db.commit()

    def _both_signed_in_with_data(self) -> tuple[str, str]:
        alice_token = self._sign_in(ALICE)
        alice_owner = self._owner_of(ALICE)
        bob_token = self._sign_in(BOB)
        bob_owner = self._owner_of(BOB)
        self._seed_candidate(alice_owner, "ALICE-ONLY")
        self._seed_candidate(bob_owner, "BOB-ONLY")
        return alice_token, bob_token

    def _as(self, token: str) -> None:
        """Become this user.

        Clearing first matters: httpx keeps both cookies rather than replacing
        one of the same name, and the stale one won. That looked exactly like a
        cross-tenant leak until the jar was inspected.
        """
        self.client.cookies.clear()
        self.client.cookies.set(settings.session_cookie_name, token)

    def _subjects(self) -> list[str]:
        body = self.client.get("/candidates", params={"state": "needs_review"}).json()
        return [item["subject"] for item in body.get("items", [])]

    # -- the guarantee ---------------------------------------------------

    def test_each_user_sees_only_their_own_candidates(self) -> None:
        alice_token, bob_token = self._both_signed_in_with_data()

        self._as(alice_token)
        alice_sees = self._subjects()
        self._as(bob_token)
        bob_sees = self._subjects()

        self.assertEqual(alice_sees, ["ALICE-ONLY"])
        self.assertEqual(bob_sees, ["BOB-ONLY"])

    def test_neither_user_sees_the_configured_constant_owners_rows(self) -> None:
        """The failure mode the sweep exists to remove.

        Before B5 every service reads `settings.owner_id`, so both signed-in
        users are served the constant owner's rows - which is both a leak and,
        for them, simply the wrong data.
        """
        alice_token, _ = self._both_signed_in_with_data()
        self._seed_candidate(settings.owner_id, "CONSTANT-OWNER-ONLY")

        self._as(alice_token)

        self.assertNotIn("CONSTANT-OWNER-ONLY", self._subjects())

    def test_signing_out_stops_returning_that_users_rows(self) -> None:
        alice_token, _ = self._both_signed_in_with_data()
        self._as(alice_token)
        self.assertEqual(self._subjects(), ["ALICE-ONLY"])

        self.client.post("/auth/logout")

        self.assertNotIn("ALICE-ONLY", self._subjects())

    def test_a_client_supplied_owner_id_cannot_reach_another_users_rows(self) -> None:
        """Rule 1, as an executable check rather than a convention."""
        alice_token, _ = self._both_signed_in_with_data()
        bob_owner = self._owner_of(BOB)
        self._as(alice_token)

        for params in ({"owner_id": bob_owner}, {"ownerId": bob_owner}, {"owner": bob_owner}):
            with self.subTest(params=params):
                body = self.client.get(
                    "/candidates", params={"state": "needs_review", **params}
                ).json()
                subjects = [item["subject"] for item in body.get("items", [])]
                self.assertNotIn("BOB-ONLY", subjects)

    def test_an_owner_id_header_cannot_reach_another_users_rows(self) -> None:
        alice_token, _ = self._both_signed_in_with_data()
        bob_owner = self._owner_of(BOB)
        self._as(alice_token)

        body = self.client.get(
            "/candidates", params={"state": "needs_review"}, headers={"X-Owner-Id": bob_owner}
        ).json()

        self.assertNotIn("BOB-ONLY", [item["subject"] for item in body.get("items", [])])

    def test_with_sign_in_off_the_single_tenant_behaviour_is_unchanged(self) -> None:
        """The revert route: the flag must restore exactly today's behaviour."""
        settings.feature_auth_enabled = False
        self._seed_candidate(settings.owner_id, "CONSTANT-OWNER-ONLY")

        self.assertEqual(self._subjects(), ["CONSTANT-OWNER-ONLY"])


if __name__ == "__main__":
    unittest.main()


class SingletonOwnerCaptureTests(CrossTenantIsolationTests):
    """The leak the first isolation tests missed, and the shape of it.

    `/candidates` filters inside the request, so it was correct as soon as the
    owner_id sweep landed. `/inbox/conversations` goes through
    `_get_orchestration_service()`, a **module-level singleton** that captured
    `tenancy.owner_id()` once, on the first request, and then served every user
    with that owner. A second test user saw the first one's entire inbox.

    The sweep could not have found this: the call site read
    `tenancy.owner_id()`, which is correct - it was simply evaluated once and
    cached. Any long-lived object that stores an owner has the same defect, so
    what is tested here is *ordering*, not just the value.
    """

    def _seed_conversation(self, owner_id: str, thread: str) -> None:
        from app.models import EmailConversation

        with Session(self.engine) as db:
            db.add(
                EmailConversation(
                    owner_id=owner_id,
                    external_thread_id=thread,
                    origin="label",
                    subject_snapshot=thread,
                    recruiter_snapshot="R",
                    recruiter_email_snapshot=f"{thread}@example.com",
                    last_message_at=datetime.now(UTC),
                    unread_reply_count=0,
                    status="replied",
                )
            )
            db.commit()

    def _threads(self) -> list[str]:
        body = self.client.get("/inbox/conversations", params={"limit": 50}).json()
        return sorted(row["subject"] for row in body)

    def test_a_singleton_backed_endpoint_does_not_serve_the_first_callers_owner(self) -> None:
        alice_token = self._sign_in(ALICE)
        alice_owner = self._owner_of(ALICE)
        bob_token = self._sign_in(BOB)
        bob_owner = self._owner_of(BOB)
        self._seed_conversation(alice_owner, "ALICE-THREAD")
        self._seed_conversation(bob_owner, "BOB-THREAD")

        # Alice calls first, so she is the owner any singleton would capture.
        self._as(alice_token)
        alice_sees = self._threads()
        self._as(bob_token)
        bob_sees = self._threads()

        self.assertEqual(alice_sees, ["ALICE-THREAD"])
        self.assertEqual(bob_sees, ["BOB-THREAD"], "Bob was served the first caller's owner")

    def test_the_result_is_the_same_whichever_user_calls_first(self) -> None:
        """Order-dependence is the signature of a captured owner."""
        alice_token = self._sign_in(ALICE)
        alice_owner = self._owner_of(ALICE)
        bob_token = self._sign_in(BOB)
        bob_owner = self._owner_of(BOB)
        self._seed_conversation(alice_owner, "ALICE-THREAD")
        self._seed_conversation(bob_owner, "BOB-THREAD")

        self._as(bob_token)
        bob_first = self._threads()
        self._as(alice_token)
        alice_second = self._threads()
        self._as(bob_token)
        bob_again = self._threads()

        self.assertEqual(bob_first, ["BOB-THREAD"])
        self.assertEqual(alice_second, ["ALICE-THREAD"])
        self.assertEqual(bob_again, bob_first, "the second caller contaminated the first")


class UnauthenticatedAccessTests(CrossTenantIsolationTests):
    """With sign-in on, no session means no data. Found in live use.

    The login page is a *frontend* gate: it decides what to render and never
    stopped the API answering. An incognito window with no cookie was served
    the configured owner's data in full, because `tenancy.owner_id()` falls
    back to the constant - correct while the flag is off, a hole the moment it
    is on.
    """

    def test_an_anonymous_request_is_refused_rather_than_served_the_fallback(self) -> None:
        self._seed_candidate(settings.owner_id, "CONSTANT-OWNER-ONLY")
        self.client.cookies.clear()

        response = self.client.get("/candidates", params={"state": "needs_review"})

        self.assertEqual(response.status_code, 401)
        self.assertNotIn("CONSTANT-OWNER-ONLY", response.text)

    def test_a_made_up_cookie_is_refused(self) -> None:
        self.client.cookies.clear()
        self.client.cookies.set(settings.session_cookie_name, "not-a-real-token")

        self.assertEqual(
            self.client.get("/candidates", params={"state": "needs_review"}).status_code, 401
        )

    def test_a_revoked_session_stops_reaching_data(self) -> None:
        alice_token, _ = self._both_signed_in_with_data()
        self._as(alice_token)
        self.assertEqual(self._subjects(), ["ALICE-ONLY"])

        self.client.post("/auth/logout")
        self._as(alice_token)

        self.assertEqual(
            self.client.get("/candidates", params={"state": "needs_review"}).status_code, 401
        )

    def test_sign_in_routes_stay_reachable_without_a_session(self) -> None:
        """Otherwise nobody could ever obtain one."""
        self.client.cookies.clear()

        self.assertEqual(self.client.get("/auth/google/start").status_code, 200)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_the_open_tracking_pixel_stays_public(self) -> None:
        """It is fetched by a recruiter's mail client, which has no session."""
        self.client.cookies.clear()

        response = self.client.get("/track/open/sometoken.png")

        self.assertNotEqual(response.status_code, 401)

    def test_the_health_probe_stays_public(self) -> None:
        self.client.cookies.clear()

        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_with_sign_in_off_anonymous_access_still_works(self) -> None:
        """The revert route: turning the flag off restores single-tenant use."""
        settings.feature_auth_enabled = False
        self._seed_candidate(settings.owner_id, "CONSTANT-OWNER-ONLY")
        self.client.cookies.clear()

        self.assertEqual(self._subjects(), ["CONSTANT-OWNER-ONLY"])
