"""Storage and encryption for per-owner Gmail OAuth credentials.

Deliberately a service rather than more code in `gmail_client.py`: that module
is ~900 lines and already mixes OAuth, MIME construction and Gmail API calls.
Persistence and cryptography do not belong in it.

The import direction is one-way and matters. `gmail_client` imports this
module; this module must never import `gmail_client`, or the two form a cycle.
That is why `save_credentials` takes `google_email` as an argument instead of
calling `users.getProfile` itself - the Gmail call lives on the other side of
the boundary and passes its result in.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from google.oauth2.credentials import Credentials
from sqlalchemy.orm import Session

from app.config import settings
from app.models import GmailCredential
from app.services.secret_crypto import CredentialEncryptionUnavailable, decrypt, encrypt

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GmailConnectionStatus:
    """What the UI and the renewal cron are allowed to know.

    No token material, encrypted or otherwise. Keeping that out of the type
    means no future edit can leak one through a response model by accident.
    """

    owner_id: str
    connected: bool
    google_email: str = ""
    expires_at: datetime | None = None
    connected_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    revoked: bool = False
    last_error: str = ""
    scopes: tuple[str, ...] = ()


def get_row(db: Session, owner_id: str) -> GmailCredential | None:
    return db.query(GmailCredential).filter(GmailCredential.owner_id == owner_id).first()


def get_credentials(db: Session, owner_id: str) -> Credentials | None:
    """Reassemble a usable `Credentials`, or None when there is nothing to use.

    Does not refresh. The caller decides, because only the caller knows whether
    it is in a position to handle a refresh failure.

    A revoked row returns None rather than a credential, so no code path can
    use one by forgetting to check `revoked_at`.
    """
    row = get_row(db, owner_id)
    if row is None or row.revoked_at is not None:
        return None
    refresh_token = decrypt(row.refresh_token_encrypted)
    access_token = decrypt(row.access_token_encrypted)
    if not (refresh_token or access_token):
        return None
    credentials = Credentials(
        token=access_token or None,
        refresh_token=refresh_token or None,
        token_uri=row.token_uri,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=json.loads(row.scopes_json or "[]"),
    )
    # google-auth compares `expiry` against a naive UTC now, so handing it an
    # aware datetime raises "can't compare offset-naive and offset-aware".
    if row.expires_at is not None:
        expiry = row.expires_at
        credentials.expiry = expiry.replace(tzinfo=None) if expiry.tzinfo else expiry
    return credentials


def save_credentials(
    db: Session,
    owner_id: str,
    credentials: Credentials,
    *,
    google_email: str = "",
    google_subject: str | None = None,
) -> GmailCredential:
    """Upsert one owner's connection.

    The refresh-token branch below is the most important line in this file.
    Google returns `refresh_token` only on the first consent, or when
    `prompt=consent` forces it. Every subsequent re-consent - which, in
    Testing publishing status, is every seven days - comes back without one.
    Persisting `credentials.refresh_token` unconditionally would therefore
    write a null over a working token and silently kill silent refresh. The
    breakage would not show for another week, which is exactly what makes it
    dangerous, so it has its own regression test.
    """
    row = get_row(db, owner_id)
    if row is None:
        row = GmailCredential(owner_id=owner_id)
        db.add(row)

    row.access_token_encrypted = encrypt(credentials.token or "")
    if credentials.refresh_token:
        row.refresh_token_encrypted = encrypt(credentials.refresh_token)
    # else: keep whatever is stored. See the docstring.

    if credentials.token_uri:
        row.token_uri = credentials.token_uri
    if credentials.scopes:
        row.scopes_json = json.dumps(sorted(credentials.scopes))
    row.expires_at = _as_utc(credentials.expiry)
    if google_email:
        row.google_email = google_email[:320]
    if google_subject:
        row.google_subject = google_subject[:64]
    # A successful save is a working connection by definition.
    row.revoked_at = None
    row.last_error = ""
    db.flush()
    logger.info("gmail_credentials_saved owner_id=%s email=%s", owner_id, row.google_email or "unknown")
    return row


def mark_refreshed(db: Session, owner_id: str, credentials: Credentials) -> None:
    row = get_row(db, owner_id)
    if row is None:
        return
    row.access_token_encrypted = encrypt(credentials.token or "")
    row.expires_at = _as_utc(credentials.expiry)
    row.last_refreshed_at = datetime.now(UTC)
    row.last_error = ""
    db.flush()


def mark_revoked(db: Session, owner_id: str, reason: str) -> None:
    """Retire a connection and clear the token material.

    Clearing the ciphertext is not housekeeping. A row flagged revoked while
    still holding a usable refresh token is a credential nobody is watching -
    the flag stops this application using it and stops nothing else.
    """
    row = get_row(db, owner_id)
    if row is None:
        return
    row.revoked_at = row.revoked_at or datetime.now(UTC)
    row.access_token_encrypted = ""
    row.refresh_token_encrypted = None
    row.last_error = (reason or "")[:2000]
    db.flush()
    logger.warning("gmail_credentials_revoked owner_id=%s reason=%s", owner_id, row.last_error)


def connection_status(db: Session, owner_id: str) -> GmailConnectionStatus:
    row = get_row(db, owner_id)
    if row is None:
        return GmailConnectionStatus(owner_id=owner_id, connected=False)
    return GmailConnectionStatus(
        owner_id=owner_id,
        connected=row.revoked_at is None and bool(row.refresh_token_encrypted or row.access_token_encrypted),
        google_email=row.google_email or "",
        expires_at=row.expires_at,
        connected_at=row.connected_at,
        last_refreshed_at=row.last_refreshed_at,
        revoked=row.revoked_at is not None,
        last_error=row.last_error or "",
        scopes=tuple(json.loads(row.scopes_json or "[]")),
    )


def list_connections(db: Session) -> list[GmailConnectionStatus]:
    """Every stored connection. The renewal cron (capability 5) needs this."""
    return [
        connection_status(db, row.owner_id)
        for row in db.query(GmailCredential).order_by(GmailCredential.owner_id)
    ]


def import_legacy_token_file(db: Session, owner_id: str, *, google_email: str = "") -> bool:
    """Adopt an existing `google_token.json` into the table, once.

    Runs in the service rather than in migration 0076 for two reasons: a
    migration executes in containers that may not carry the encryption key,
    and decryption logic inside a migration is neither testable nor replayable.

    Returns True only when a row was created, so the caller can re-read.
    Idempotent by construction - an existing row short-circuits it.
    """
    if get_row(db, owner_id) is not None:
        return False

    from pathlib import Path

    token_path = Path(settings.google_token_path)
    if not token_path.exists():
        return False

    try:
        payload = json.loads(token_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("gmail_credentials_import_unreadable path=%s", token_path, exc_info=True)
        return False

    refresh_token = (payload.get("refresh_token") or "").strip()
    access_token = (payload.get("token") or "").strip()
    if not (refresh_token or access_token):
        logger.warning("gmail_credentials_import_empty path=%s", token_path)
        return False

    row = GmailCredential(
        owner_id=owner_id,
        access_token_encrypted=encrypt(access_token),
        refresh_token_encrypted=encrypt(refresh_token) if refresh_token else None,
        token_uri=payload.get("token_uri") or "https://oauth2.googleapis.com/token",
        scopes_json=json.dumps(sorted(payload.get("scopes") or [])),
        expires_at=_parse_expiry(payload.get("expiry")),
        # Empty when the caller could not resolve it. Deliberately not fatal:
        # a getProfile hiccup must not cost the owner their stored token, and
        # the address is backfilled on the next successful save.
        google_email=(google_email or payload.get("account") or "")[:320],
    )
    db.add(row)
    db.flush()

    # Renamed, never deleted. If this import turns out to be wrong the
    # credential is still on disk and recoverable.
    try:
        token_path.rename(token_path.with_suffix(token_path.suffix + ".imported"))
    except OSError:
        logger.warning("gmail_credentials_import_rename_failed path=%s", token_path, exc_info=True)

    logger.info("gmail_credentials_imported owner_id=%s email=%s", owner_id, row.google_email or "unknown")
    return True


def _parse_expiry(value: object) -> datetime | None:
    """google-auth writes expiry as a naive UTC ISO string, sometimes with Z."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime | None) -> datetime | None:
    """google-auth hands back a naive UTC expiry; the column is timezone-aware."""
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
