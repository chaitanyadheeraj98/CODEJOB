"""Adopting the existing google_token.json into the credentials table.

The point of this step is that switching storage must not cost the owner a
reconnect. It runs in the service rather than in migration 0076 because a
migration executes where the encryption key may not be, and decryption inside
a migration is neither testable nor replayable.
"""

import json
import unittest
from datetime import UTC
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import GmailCredential
from app.services import gmail_credential_service as service

KEY = Fernet.generate_key().decode()
TOKEN_PAYLOAD = {
    "token": "access-from-file",
    "refresh_token": "refresh-from-file",
    "token_uri": "https://oauth2.googleapis.com/token",
    "client_id": "cid",
    "client_secret": "secret",
    "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
    "expiry": "2026-09-10T19:42:42.994143Z",
    "account": "owner@example.com",
}


class LegacyTokenImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.tmp = TemporaryDirectory()
        self.token_path = Path(self.tmp.name) / "google_token.json"
        self._previous = (settings.credential_encryption_key, settings.google_token_path)
        settings.credential_encryption_key = KEY
        settings.google_token_path = str(self.token_path)

    def tearDown(self) -> None:
        settings.credential_encryption_key, settings.google_token_path = self._previous
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _write(self, payload: object) -> None:
        self.token_path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
        )

    def test_imports_a_valid_file(self) -> None:
        self._write(TOKEN_PAYLOAD)

        self.assertTrue(service.import_legacy_token_file(self.db, "owner"))

        row = self.db.query(GmailCredential).one()
        self.assertEqual(service.decrypt(row.refresh_token_encrypted), "refresh-from-file")
        self.assertEqual(service.decrypt(row.access_token_encrypted), "access-from-file")
        self.assertEqual(row.google_email, "owner@example.com")
        self.assertEqual(json.loads(row.scopes_json), TOKEN_PAYLOAD["scopes"])

    def test_the_imported_credential_is_immediately_usable(self) -> None:
        self._write(TOKEN_PAYLOAD)

        service.import_legacy_token_file(self.db, "owner")

        credentials = service.get_credentials(self.db, "owner")
        assert credentials is not None
        self.assertEqual(credentials.refresh_token, "refresh-from-file")

    def test_the_expiry_is_parsed_and_stored_as_utc(self) -> None:
        self._write(TOKEN_PAYLOAD)

        service.import_legacy_token_file(self.db, "owner")

        expires_at = self.db.query(GmailCredential).one().expires_at
        assert expires_at is not None
        self.assertEqual(expires_at.astimezone(UTC).hour, 19)

    def test_the_file_is_renamed_not_deleted(self) -> None:
        """If the import is wrong the credential must still be recoverable."""
        self._write(TOKEN_PAYLOAD)

        service.import_legacy_token_file(self.db, "owner")

        self.assertFalse(self.token_path.exists())
        self.assertTrue(self.token_path.with_suffix(".json.imported").exists())

    def test_is_a_no_op_when_a_row_already_exists(self) -> None:
        self._write(TOKEN_PAYLOAD)
        service.import_legacy_token_file(self.db, "owner")

        self.assertFalse(service.import_legacy_token_file(self.db, "owner"))
        self.assertEqual(self.db.query(GmailCredential).count(), 1)

    def test_is_a_no_op_when_the_file_is_missing(self) -> None:
        self.assertFalse(service.import_legacy_token_file(self.db, "owner"))
        self.assertEqual(self.db.query(GmailCredential).count(), 0)

    def test_survives_a_corrupt_file(self) -> None:
        self._write("{not valid json")

        self.assertFalse(service.import_legacy_token_file(self.db, "owner"))
        self.assertEqual(self.db.query(GmailCredential).count(), 0)
        self.assertTrue(self.token_path.exists(), "a file we could not read is left alone")

    def test_refuses_a_file_with_no_tokens_in_it(self) -> None:
        self._write({"client_id": "cid", "scopes": []})

        self.assertFalse(service.import_legacy_token_file(self.db, "owner"))
        self.assertEqual(self.db.query(GmailCredential).count(), 0)

    def test_imports_when_only_a_refresh_token_is_present(self) -> None:
        """The access token is an hour old at best; the refresh token is what matters."""
        self._write({k: v for k, v in TOKEN_PAYLOAD.items() if k != "token"})

        self.assertTrue(service.import_legacy_token_file(self.db, "owner"))
        row = self.db.query(GmailCredential).one()
        self.assertEqual(service.decrypt(row.refresh_token_encrypted), "refresh-from-file")

    def test_a_caller_supplied_email_wins_over_the_files_account_field(self) -> None:
        self._write(TOKEN_PAYLOAD)

        service.import_legacy_token_file(self.db, "owner", google_email="live@example.com")

        self.assertEqual(self.db.query(GmailCredential).one().google_email, "live@example.com")

    def test_a_missing_email_is_not_fatal(self) -> None:
        """A getProfile hiccup must not cost the owner their stored token."""
        self._write({k: v for k, v in TOKEN_PAYLOAD.items() if k != "account"})

        self.assertTrue(service.import_legacy_token_file(self.db, "owner"))
        self.assertEqual(self.db.query(GmailCredential).one().google_email, "")

    def test_a_bad_expiry_string_does_not_block_the_import(self) -> None:
        self._write({**TOKEN_PAYLOAD, "expiry": "not-a-date"})

        self.assertTrue(service.import_legacy_token_file(self.db, "owner"))
        self.assertIsNone(self.db.query(GmailCredential).one().expires_at)


if __name__ == "__main__":
    unittest.main()
