"""Shared encryption for stored credentials."""

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class CredentialEncryptionUnavailable(RuntimeError):
    """Raised when the configured Fernet key cannot safely be used."""


def _fernet() -> Fernet:
    key = (settings.credential_encryption_key or "").strip()
    if not key:
        raise CredentialEncryptionUnavailable(
            "CREDENTIAL_ENCRYPTION_KEY is not set; refusing to handle credentials."
        )
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise CredentialEncryptionUnavailable(
            "CREDENTIAL_ENCRYPTION_KEY is not a valid Fernet key."
        ) from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(value: str | None) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CredentialEncryptionUnavailable(
            "Stored credential could not be decrypted with the current "
            "CREDENTIAL_ENCRYPTION_KEY. The key has changed, or the row is corrupt."
        ) from exc
