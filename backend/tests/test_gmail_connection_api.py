"""A7 — truthful connection status, and the connection API.

The bug this closes: `gmail_auth_status` reported `creds.valid`, which is False
the moment the access token passes its hour. Settings therefore read "Not
authenticated" for a healthy connection roughly 23 hours out of 24, which is
the leading suspect for the hourly "Connect Gmail" prompt in §19 of temp176.
"""

import os
import unittest
from datetime import UTC, datetime, timedelta
from tempfile import TemporaryDirectory
from pathlib import Path

os.environ["DEBUG"] = "false"

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database, gmail_client, main
from app.db import Base
from app.models import GmailCredential, UserSettings
from app.services import gmail_credential_service as service

KEY = Fernet.generate_key().decode()


def _credentials(*, expired: bool = False, refresh: str | None = "refresh-1") -> Credentials:
    creds = Credentials(
        token="access-1",
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client",
        client_secret="secret",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    creds.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return creds


class GmailConnectionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)
        self.tmp = TemporaryDirectory()

        def override_get_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.owner = main.settings.owner_id
        self._previous = (
            main.settings.feature_db_credentials_enabled,
            main.settings.credential_encryption_key,
            main.settings.google_token_path,
            main.settings.google_client_id,
            main.settings.google_client_secret,
            main.settings.google_redirect_uri,
            database.SessionLocal,
        )
        main.settings.feature_db_credentials_enabled = True
        main.settings.credential_encryption_key = KEY
        main.settings.google_token_path = str(Path(self.tmp.name) / "google_token.json")
        main.settings.google_client_id = "client"
        main.settings.google_client_secret = "secret"
        main.settings.google_redirect_uri = "http://localhost:8080/"
        database.SessionLocal = self.SessionLocal
        with Session(self.engine) as db:
            db.add(UserSettings(owner_id=self.owner))
            db.commit()

    def tearDown(self) -> None:
        (
            main.settings.feature_db_credentials_enabled,
            main.settings.credential_encryption_key,
            main.settings.google_token_path,
            main.settings.google_client_id,
            main.settings.google_client_secret,
            main.settings.google_redirect_uri,
            database.SessionLocal,
        ) = self._previous
        main.app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _store(self, creds: Credentials, email: str = "owner@example.com") -> None:
        with Session(self.engine) as db:
            service.save_credentials(db, self.owner, creds, google_email=email)
            db.commit()

    # -- the bug ---------------------------------------------------------

    def test_an_expired_access_token_with_a_refresh_token_is_still_connected(self) -> None:
        """The whole point of A7. This used to report "Not authenticated"."""
        self._store(_credentials(expired=True))

        body = self.client.get("/gmail/status").json()

        self.assertTrue(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_CONNECTED_REFRESHABLE)

    def test_a_fresh_access_token_is_connected(self) -> None:
        self._store(_credentials())

        body = self.client.get("/gmail/status").json()

        self.assertTrue(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_CONNECTED)

    def test_status_reports_the_account_address_not_a_file_path(self) -> None:
        self._store(_credentials(), email="someone@example.com")

        body = self.client.get("/gmail/status").json()

        self.assertEqual(body["account_email"], "someone@example.com")

    def test_no_credential_reads_as_not_connected_not_as_needing_reconnect(self) -> None:
        body = self.client.get("/gmail/status").json()

        self.assertFalse(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_NOT_CONNECTED)

    def test_a_revoked_credential_reads_as_needing_reconnect(self) -> None:
        self._store(_credentials())
        with Session(self.engine) as db:
            service.mark_revoked(db, self.owner, "invalid_grant")
            db.commit()

        body = self.client.get("/gmail/status").json()

        self.assertFalse(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_NEEDS_RECONNECT)

    # -- /gmail/connection ----------------------------------------------

    def test_connection_reports_the_details_a_reconnect_affordance_needs(self) -> None:
        self._store(_credentials())

        body = self.client.get("/gmail/connection").json()

        self.assertTrue(body["connected"])
        self.assertEqual(body["google_email"], "owner@example.com")
        self.assertFalse(body["revoked"])
        self.assertIn("gmail.modify", body["scopes"][0])

    def test_the_connection_body_carries_no_token_material(self) -> None:
        """Asserted on the serialised body, not the model."""
        self._store(_credentials())

        raw = self.client.get("/gmail/connection").text

        self.assertNotIn("refresh-1", raw)
        self.assertNotIn("access-1", raw)
        for word in ("refresh_token", "access_token", "client_secret"):
            self.assertNotIn(word, raw)

    def test_disconnect_clears_the_ciphertext_not_just_the_flag(self) -> None:
        self._store(_credentials())

        body = self.client.post("/gmail/connection/disconnect").json()

        self.assertFalse(body["connected"])
        self.assertTrue(body["revoked"])
        with Session(self.engine) as db:
            row = db.query(GmailCredential).one()
            self.assertEqual(row.access_token_encrypted, "")
            self.assertIsNone(row.refresh_token_encrypted)

    def test_disconnect_is_idempotent(self) -> None:
        self._store(_credentials())
        first = self.client.post("/gmail/connection/disconnect").json()

        second = self.client.post("/gmail/connection/disconnect").json()

        self.assertEqual(first["revoked"], second["revoked"])

    def test_disconnect_is_refused_when_the_feature_is_off(self) -> None:
        main.settings.feature_db_credentials_enabled = False

        self.assertEqual(self.client.post("/gmail/connection/disconnect").status_code, 409)

    # -- flag-off path ---------------------------------------------------

    def test_the_file_path_also_reports_refreshable_rather_than_unauthenticated(self) -> None:
        """The same lie existed on the file path; fix both or the revert regresses."""
        main.settings.feature_db_credentials_enabled = False
        Path(main.settings.google_token_path).write_text(
            _credentials(expired=True).to_json(), encoding="utf-8"
        )

        body = self.client.get("/gmail/status").json()

        self.assertTrue(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_CONNECTED_REFRESHABLE)

    def test_a_file_token_with_no_refresh_token_needs_a_reconnect(self) -> None:
        main.settings.feature_db_credentials_enabled = False
        Path(main.settings.google_token_path).write_text(
            _credentials(expired=True, refresh=None).to_json(), encoding="utf-8"
        )

        body = self.client.get("/gmail/status").json()

        self.assertFalse(body["authenticated"])
        self.assertEqual(body["state"], gmail_client.GMAIL_STATE_NEEDS_RECONNECT)


if __name__ == "__main__":
    unittest.main()
