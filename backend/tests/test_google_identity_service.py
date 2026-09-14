"""B2 — verifying who signed in, and that they own the mailbox they attached.

Two rules carry the weight here:

  1. An ID token is *verified*, never merely decoded. On a real callback the
     token arrives from a browser following a redirect, so it is
     attacker-controlled input until Google's signature says otherwise.
  2. The signed-in identity and the connected mailbox must be the same account,
     or a user could sign in as themselves and attach somebody else's mailbox -
     and every owner_id row written afterwards would be filed under the wrong
     person.
"""

import unittest
from unittest.mock import patch

from app.config import settings
from app.services import google_identity_service as service
from app.services.google_identity_service import IdentityVerificationError, VerifiedIdentity

CLAIMS = {
    "iss": "https://accounts.google.com",
    "sub": "1234567890",
    "email": "Owner@Example.com",
    "email_verified": True,
    "name": "Owner Example",
}


class VerifyIdTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = settings.google_client_id
        settings.google_client_id = "client-id.apps.googleusercontent.com"

    def tearDown(self) -> None:
        settings.google_client_id = self._previous

    def _verify(self, claims=None, raw="token"):
        with patch.object(service.google_id_token, "verify_oauth2_token", return_value=claims or CLAIMS):
            return service.verify_id_token(raw)

    def test_returns_the_claims_it_is_willing_to_act_on(self) -> None:
        identity = self._verify()

        self.assertEqual(identity.subject, "1234567890")
        self.assertTrue(identity.email_verified)
        self.assertEqual(identity.name, "Owner Example")

    def test_the_email_is_lowercased(self) -> None:
        """It is compared against a mailbox address and stored as an identity."""
        self.assertEqual(self._verify().email, "owner@example.com")

    def test_the_audience_is_checked_against_our_client_id(self) -> None:
        """Skipping the audience would accept a token minted for another app."""
        with patch.object(service.google_id_token, "verify_oauth2_token", return_value=CLAIMS) as verify:
            service.verify_id_token("token")

        self.assertEqual(verify.call_args.kwargs["audience"], settings.google_client_id)

    def test_a_signature_failure_is_refused_and_never_quotes_the_token(self) -> None:
        with patch.object(
            service.google_id_token, "verify_oauth2_token", side_effect=ValueError("bad token: secret-jwt")
        ):
            with self.assertRaises(IdentityVerificationError) as caught:
                service.verify_id_token("secret-jwt")

        self.assertNotIn("secret-jwt", str(caught.exception))

    def test_an_unexpected_issuer_is_refused(self) -> None:
        with self.assertRaises(IdentityVerificationError):
            self._verify({**CLAIMS, "iss": "https://evil.example.com"})

    def test_both_google_issuers_are_accepted(self) -> None:
        for issuer in ("accounts.google.com", "https://accounts.google.com"):
            with self.subTest(issuer=issuer):
                self.assertEqual(self._verify({**CLAIMS, "iss": issuer}).subject, "1234567890")

    def test_an_unverified_email_is_refused(self) -> None:
        """The access model is "this address is on the list"; unverified is not an identity."""
        with self.assertRaises(IdentityVerificationError):
            self._verify({**CLAIMS, "email_verified": False})

    def test_missing_claims_are_refused(self) -> None:
        for missing in ("sub", "email"):
            with self.subTest(missing=missing):
                with self.assertRaises(IdentityVerificationError):
                    self._verify({**CLAIMS, missing: ""})

    def test_an_empty_token_is_refused_without_calling_google(self) -> None:
        with patch.object(service.google_id_token, "verify_oauth2_token") as verify:
            with self.assertRaises(IdentityVerificationError):
                service.verify_id_token("")

        verify.assert_not_called()

    def test_verification_is_refused_with_no_client_id_configured(self) -> None:
        settings.google_client_id = ""

        with self.assertRaises(IdentityVerificationError):
            service.verify_id_token("token")


class MailboxMatchTests(unittest.TestCase):
    identity = VerifiedIdentity(subject="s", email="owner@example.com", email_verified=True)

    def test_the_same_account_passes(self) -> None:
        service.assert_identity_matches_mailbox(self.identity, "owner@example.com")

    def test_case_and_whitespace_do_not_cause_a_false_mismatch(self) -> None:
        service.assert_identity_matches_mailbox(self.identity, "  Owner@Example.COM ")

    def test_a_different_mailbox_is_refused(self) -> None:
        """Signing in as yourself must not let you attach another account's mail."""
        with self.assertRaises(IdentityVerificationError):
            service.assert_identity_matches_mailbox(self.identity, "someone.else@example.com")

    def test_an_unknown_mailbox_is_a_mismatch_not_a_pass(self) -> None:
        """A getProfile failure is tolerable for a display name, not for evidence."""
        with self.assertRaises(IdentityVerificationError):
            service.assert_identity_matches_mailbox(self.identity, "")

    def test_the_mismatch_message_names_no_address(self) -> None:
        with self.assertRaises(IdentityVerificationError) as caught:
            service.assert_identity_matches_mailbox(self.identity, "someone.else@example.com")

        self.assertNotIn("someone.else", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
