"""Verify who actually signed in, from the ID token Google returns.

Separate from `gmail_credential_service` because it verifies rather than
stores, and separate from `gmail_client` because it makes no Gmail call. It is
the one place that decides "this is the person", so it is the one place that
must never take a shortcut.

The shortcut in question: an ID token is a JWT, and decoding one is a single
line. Decoding is **not** verification. An unverified token is a string the
caller handed us, and the caller on a real callback is a browser following a
redirect. `verify_oauth2_token` checks the signature against Google's public
keys, the audience against our client id, the issuer, and the expiry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from app.config import settings

logger = logging.getLogger(__name__)

# Google mints ID tokens with either issuer; both are legitimate.
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")


class IdentityVerificationError(RuntimeError):
    """The ID token could not be trusted. Never recoverable by retrying."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """Only the claims we are willing to act on."""

    subject: str
    email: str
    email_verified: bool
    name: str = ""


def verify_id_token(raw_token: str) -> VerifiedIdentity:
    if not raw_token:
        raise IdentityVerificationError("No ID token was returned. Were the identity scopes requested?")
    if not settings.google_client_id:
        raise IdentityVerificationError("Cannot verify an ID token without GOOGLE_CLIENT_ID.")

    try:
        claims = google_id_token.verify_oauth2_token(
            raw_token,
            google_requests.Request(),
            audience=settings.google_client_id,
        )
    except Exception as exc:
        # Type only. A verification failure message can quote the token.
        logger.warning("google_id_token_verification_failed error=%s", type(exc).__name__)
        raise IdentityVerificationError("The Google ID token could not be verified.") from exc

    if claims.get("iss") not in _ISSUERS:
        raise IdentityVerificationError("The Google ID token has an unexpected issuer.")

    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "").strip().lower()
    if not subject or not email:
        raise IdentityVerificationError("The Google ID token carried no subject or email.")

    # `email_verified` false means Google itself does not vouch for the address.
    # Refused rather than warned about: the whole access model is "this address
    # is on the test-user list", and an unverified address is not an identity.
    email_verified = bool(claims.get("email_verified"))
    if not email_verified:
        raise IdentityVerificationError("Google has not verified that email address.")

    return VerifiedIdentity(
        subject=subject,
        email=email,
        email_verified=email_verified,
        name=str(claims.get("name") or ""),
    )


def assert_identity_matches_mailbox(identity: VerifiedIdentity, mailbox_email: str) -> None:
    """The signed-in person and the connected mailbox must be the same account.

    Without this check a user could sign in as themselves and attach somebody
    else's mailbox, and every `owner_id` row written afterwards would be filed
    under the wrong person. There is no partial credit here: a mismatch
    discards the tokens.

    An empty `mailbox_email` is treated as a mismatch rather than waved
    through. `getProfile` failing is tolerable when it only costs a display
    name (see `gmail_client._profile_email`); it is not tolerable when it is
    the evidence this check exists to weigh.
    """
    mailbox = (mailbox_email or "").strip().lower()
    if not mailbox:
        raise IdentityVerificationError(
            "Could not read the mailbox address to compare against the signed-in account."
        )
    if mailbox != identity.email:
        logger.warning("google_identity_mailbox_mismatch subject=%s", identity.subject)
        raise IdentityVerificationError(
            "The Google account you signed in with is not the mailbox that was connected."
        )
