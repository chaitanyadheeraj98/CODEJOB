"""The DB-credentials flag must never start without a usable encryption key.

There is no safe degraded mode: falling back to plaintext would write Gmail
refresh tokens into Postgres, where every backup carries them. So the check is
fatal and it runs at construction, which means alembic, the worker and any
script fail the same way the API does.
"""

import unittest

from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.config import Settings

VALID_KEY = Fernet.generate_key().decode()


def _settings(**overrides: object) -> Settings:
    # _env_file=None so a developer's own backend/.env cannot decide the result.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class CredentialEncryptionConfigTests(unittest.TestCase):
    def test_the_feature_is_off_by_default(self) -> None:
        settings = _settings()

        self.assertFalse(settings.feature_db_credentials_enabled)
        self.assertEqual(settings.credential_encryption_key, "")

    def test_no_key_is_fine_while_the_feature_is_off(self) -> None:
        """The whole point of the flag: this lands without touching an install."""
        settings = _settings(feature_db_credentials_enabled=False, credential_encryption_key="")

        self.assertFalse(settings.feature_db_credentials_enabled)

    def test_the_feature_starts_with_a_valid_key(self) -> None:
        settings = _settings(feature_db_credentials_enabled=True, credential_encryption_key=VALID_KEY)

        self.assertTrue(settings.feature_db_credentials_enabled)

    def test_an_empty_key_is_refused_when_the_feature_is_on(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            _settings(feature_db_credentials_enabled=True, credential_encryption_key="")

        message = str(caught.exception)
        self.assertIn("CREDENTIAL_ENCRYPTION_KEY", message)
        # The error has to say how to fix it, or it is just an outage.
        self.assertIn("Fernet.generate_key()", message)

    def test_whitespace_does_not_count_as_a_key(self) -> None:
        with self.assertRaises(ValidationError):
            _settings(feature_db_credentials_enabled=True, credential_encryption_key="   ")

    def test_a_malformed_key_is_refused(self) -> None:
        for bad in ("not-base64!!", "c2hvcnQ=", VALID_KEY[:-4]):
            with self.subTest(key=bad):
                with self.assertRaises(ValidationError) as caught:
                    _settings(feature_db_credentials_enabled=True, credential_encryption_key=bad)

                self.assertIn("not a valid Fernet key", str(caught.exception))

    def test_a_valid_key_is_accepted_with_surrounding_whitespace(self) -> None:
        """Copy-pasting a key out of a terminal picks up a newline."""
        settings = _settings(feature_db_credentials_enabled=True, credential_encryption_key=f"  {VALID_KEY}\n")

        self.assertTrue(settings.feature_db_credentials_enabled)

    def test_the_stored_key_is_normalised_not_just_validated(self) -> None:
        """Otherwise every consumer has to remember to strip it."""
        settings = _settings(feature_db_credentials_enabled=True, credential_encryption_key=f"  {VALID_KEY}\n")

        self.assertEqual(settings.credential_encryption_key, VALID_KEY)

    def test_the_key_is_normalised_even_with_the_feature_off(self) -> None:
        settings = _settings(feature_db_credentials_enabled=False, credential_encryption_key=f" {VALID_KEY} ")

        self.assertEqual(settings.credential_encryption_key, VALID_KEY)


if __name__ == "__main__":
    unittest.main()
