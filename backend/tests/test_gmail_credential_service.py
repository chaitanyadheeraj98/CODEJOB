"""Storage and crypto for per-owner Gmail credentials.

The test that matters most here is
`test_save_preserves_the_stored_refresh_token_when_google_omits_it`. Google
returns a refresh token on first consent only; in Testing publishing status
users re-consent weekly and every one of those responses arrives without one.
Overwriting with the null would kill silent refresh, and the damage would stay
invisible for another seven days.
"""

import json
import unittest
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import GmailCredential
from app.services import gmail_credential_service as service

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _credentials(*, token="access-1", refresh="refresh-1", expiry=None) -> Credentials:
    creds = Credentials(
        token=token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="cid",
        client_secret="secret",
        scopes=SCOPES,
    )
    creds.expiry = expiry
    return creds


class GmailCredentialServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self._previous_key = settings.credential_encryption_key
        settings.credential_encryption_key = KEY

    def tearDown(self) -> None:
        settings.credential_encryption_key = self._previous_key
        self.db.close()
        self.engine.dispose()

    # -- encryption ------------------------------------------------------

    def test_round_trips_a_value(self) -> None:
        self.assertEqual(service.decrypt(service.encrypt("secret-token")), "secret-token")

    def test_the_stored_column_is_not_the_plaintext(self) -> None:
        """The entire reason this table exists rather than a file."""
        service.save_credentials(self.db, "owner", _credentials(refresh="the-refresh-token"))

        row = self.db.query(GmailCredential).one()
        self.assertNotIn("the-refresh-token", row.refresh_token_encrypted or "")
        self.assertNotEqual(row.refresh_token_encrypted, "the-refresh-token")
        self.assertEqual(service.decrypt(row.refresh_token_encrypted), "the-refresh-token")

    def test_an_empty_value_encrypts_to_empty_rather_than_ciphertext(self) -> None:
        self.assertEqual(service.encrypt(""), "")
        self.assertEqual(service.decrypt(""), "")
        self.assertEqual(service.decrypt(None), "")

    def test_a_wrong_key_raises_instead_of_returning_garbage(self) -> None:
        ciphertext = service.encrypt("secret")
        settings.credential_encryption_key = OTHER_KEY

        with self.assertRaises(service.CredentialEncryptionUnavailable):
            service.decrypt(ciphertext)

    def test_a_missing_key_refuses_to_encrypt(self) -> None:
        settings.credential_encryption_key = ""

        with self.assertRaises(service.CredentialEncryptionUnavailable):
            service.encrypt("secret")

    # -- save ------------------------------------------------------------

    def test_save_preserves_the_stored_refresh_token_when_google_omits_it(self) -> None:
        """Weekly re-consent must not null out a working refresh token."""
        service.save_credentials(self.db, "owner", _credentials(refresh="original-refresh"))

        # A re-consent response: new access token, no refresh token.
        service.save_credentials(self.db, "owner", _credentials(token="access-2", refresh=None))

        row = self.db.query(GmailCredential).one()
        self.assertEqual(service.decrypt(row.refresh_token_encrypted), "original-refresh")
        self.assertEqual(service.decrypt(row.access_token_encrypted), "access-2")

    def test_save_replaces_the_refresh_token_when_google_does_send_one(self) -> None:
        service.save_credentials(self.db, "owner", _credentials(refresh="original-refresh"))

        service.save_credentials(self.db, "owner", _credentials(refresh="rotated-refresh"))

        row = self.db.query(GmailCredential).one()
        self.assertEqual(service.decrypt(row.refresh_token_encrypted), "rotated-refresh")

    def test_save_upserts_rather_than_duplicating(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())
        service.save_credentials(self.db, "owner", _credentials(token="access-2"))

        self.assertEqual(self.db.query(GmailCredential).count(), 1)

    def test_save_records_the_account_identity(self) -> None:
        service.save_credentials(
            self.db, "owner", _credentials(), google_email="Me@Example.com", google_subject="sub-123"
        )

        row = self.db.query(GmailCredential).one()
        self.assertEqual(row.google_email, "Me@Example.com")
        self.assertEqual(row.google_subject, "sub-123")

    def test_save_keeps_a_known_email_when_a_later_save_has_none(self) -> None:
        """getProfile can fail transiently; that must not erase the address."""
        service.save_credentials(self.db, "owner", _credentials(), google_email="me@example.com")

        service.save_credentials(self.db, "owner", _credentials(token="access-2"))

        self.assertEqual(self.db.query(GmailCredential).one().google_email, "me@example.com")

    def test_save_stores_the_granted_scopes(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())

        self.assertEqual(json.loads(self.db.query(GmailCredential).one().scopes_json), SCOPES)

    def test_save_clears_a_previous_revocation(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())
        service.mark_revoked(self.db, "owner", "invalid_grant")

        service.save_credentials(self.db, "owner", _credentials(refresh="reconnected"))

        row = self.db.query(GmailCredential).one()
        self.assertIsNone(row.revoked_at)
        self.assertEqual(row.last_error, "")

    # -- read ------------------------------------------------------------

    def test_get_returns_none_for_an_unknown_owner(self) -> None:
        self.assertIsNone(service.get_credentials(self.db, "nobody"))

    def test_get_returns_none_for_a_revoked_row(self) -> None:
        """No caller can use a revoked credential by forgetting to check."""
        service.save_credentials(self.db, "owner", _credentials())
        service.mark_revoked(self.db, "owner", "invalid_grant")

        self.assertIsNone(service.get_credentials(self.db, "owner"))

    def test_get_reassembles_a_usable_credential(self) -> None:
        expiry = datetime(2026, 9, 10, 12, 0)
        service.save_credentials(self.db, "owner", _credentials(expiry=expiry))

        credentials = service.get_credentials(self.db, "owner")

        assert credentials is not None
        self.assertEqual(credentials.refresh_token, "refresh-1")
        self.assertEqual(credentials.token, "access-1")
        self.assertEqual(credentials.client_id, settings.google_client_id)
        self.assertEqual(list(credentials.scopes or []), SCOPES)

    def test_the_reassembled_expiry_is_naive_so_google_auth_can_compare_it(self) -> None:
        """google-auth compares expiry against a naive UTC now and would raise."""
        service.save_credentials(self.db, "owner", _credentials(expiry=datetime(2026, 9, 10, 12, 0)))

        credentials = service.get_credentials(self.db, "owner")

        assert credentials is not None and credentials.expiry is not None
        self.assertIsNone(credentials.expiry.tzinfo)
        # Reading `.expired` is what raises if the comparison is mismatched.
        self.assertIsInstance(credentials.expired, bool)

    # -- lifecycle -------------------------------------------------------

    def test_mark_refreshed_updates_the_access_token_and_timestamp(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())
        later = datetime.now(UTC) + timedelta(hours=1)

        service.mark_refreshed(self.db, "owner", _credentials(token="access-fresh", expiry=later.replace(tzinfo=None)))

        row = self.db.query(GmailCredential).one()
        self.assertEqual(service.decrypt(row.access_token_encrypted), "access-fresh")
        self.assertIsNotNone(row.last_refreshed_at)

    def test_mark_revoked_clears_the_token_material(self) -> None:
        """A revoked row still holding a usable token is a latent breach."""
        service.save_credentials(self.db, "owner", _credentials())

        service.mark_revoked(self.db, "owner", "invalid_grant: Token has been expired or revoked.")

        row = self.db.query(GmailCredential).one()
        self.assertIsNotNone(row.revoked_at)
        self.assertEqual(row.access_token_encrypted, "")
        self.assertIsNone(row.refresh_token_encrypted)
        self.assertIn("invalid_grant", row.last_error)

    def test_mark_revoked_is_idempotent(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())
        service.mark_revoked(self.db, "owner", "first")
        first_time = self.db.query(GmailCredential).one().revoked_at

        service.mark_revoked(self.db, "owner", "second")

        self.assertEqual(self.db.query(GmailCredential).one().revoked_at, first_time)

    def test_lifecycle_calls_on_a_missing_row_do_nothing(self) -> None:
        service.mark_refreshed(self.db, "ghost", _credentials())
        service.mark_revoked(self.db, "ghost", "reason")

        self.assertEqual(self.db.query(GmailCredential).count(), 0)

    # -- status ----------------------------------------------------------

    def test_status_reports_disconnected_with_no_row(self) -> None:
        status = service.connection_status(self.db, "owner")

        self.assertFalse(status.connected)
        self.assertFalse(status.revoked)

    def test_status_reports_a_live_connection(self) -> None:
        service.save_credentials(self.db, "owner", _credentials(), google_email="me@example.com")

        status = service.connection_status(self.db, "owner")

        self.assertTrue(status.connected)
        self.assertEqual(status.google_email, "me@example.com")
        self.assertEqual(status.scopes, tuple(SCOPES))

    def test_status_reports_a_revoked_connection_as_not_connected(self) -> None:
        service.save_credentials(self.db, "owner", _credentials())
        service.mark_revoked(self.db, "owner", "invalid_grant")

        status = service.connection_status(self.db, "owner")

        self.assertFalse(status.connected)
        self.assertTrue(status.revoked)
        self.assertIn("invalid_grant", status.last_error)

    def test_status_carries_no_token_material(self) -> None:
        service.save_credentials(self.db, "owner", _credentials(refresh="the-refresh-token"))

        status = service.connection_status(self.db, "owner")

        self.assertNotIn("the-refresh-token", repr(status))
        # Substring, not equality: a field called `access_token` would sail
        # past a check for a field called exactly `token`.
        leaky = [name for name in vars(status) if "token" in name or "secret" in name]
        self.assertEqual(leaky, [], "GmailConnectionStatus must carry no token material")

    def test_list_connections_returns_every_owner(self) -> None:
        service.save_credentials(self.db, "b-owner", _credentials())
        service.save_credentials(self.db, "a-owner", _credentials())

        self.assertEqual([c.owner_id for c in service.list_connections(self.db)], ["a-owner", "b-owner"])


if __name__ == "__main__":
    unittest.main()
