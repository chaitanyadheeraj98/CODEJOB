"""Encrypted per-owner provider credentials."""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import ProviderCredential
from app.services.secret_crypto import decrypt, encrypt


@dataclass(frozen=True)
class ProviderCredentials:
    owner_id: str
    provider: str
    api_key: str
    base_url: str = ""
    label: str = ""


class ProviderCredentialsUnavailable(RuntimeError):
    pass


def get_row(db: Session, owner_id: str, provider: str) -> ProviderCredential | None:
    return (
        db.query(ProviderCredential)
        .filter(
            ProviderCredential.owner_id == owner_id,
            ProviderCredential.provider == provider.strip().lower(),
        )
        .first()
    )


def get_credentials(db: Session, owner_id: str, provider: str) -> ProviderCredentials | None:
    row = get_row(db, owner_id, provider)
    if row is None or not row.api_key_encrypted:
        return None
    return ProviderCredentials(
        owner_id=row.owner_id,
        provider=row.provider,
        api_key=decrypt(row.api_key_encrypted),
        base_url=row.base_url or "",
        label=row.label or "",
    )


def require_credentials(db: Session, owner_id: str, provider: str) -> ProviderCredentials:
    credentials = get_credentials(db, owner_id, provider)
    if credentials is None:
        raise ProviderCredentialsUnavailable(
            f"No {provider.strip().title()} API key is configured. Add one in Settings."
        )
    return credentials


def save_credentials(
    db: Session,
    owner_id: str,
    provider: str,
    api_key: str,
    *,
    base_url: str = "",
    label: str = "",
) -> ProviderCredential:
    provider = provider.strip().lower()
    api_key = api_key.strip()
    if not provider or not api_key:
        raise ValueError("Provider and API key are required.")

    row = get_row(db, owner_id, provider)
    if row is None:
        row = ProviderCredential(owner_id=owner_id, provider=provider)
        db.add(row)
    row.api_key_encrypted = encrypt(api_key)
    row.base_url = base_url.strip()[:255]
    row.label = label.strip()[:120]
    row.last_error = ""
    db.flush()
    return row
