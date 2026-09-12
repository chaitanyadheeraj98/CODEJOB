from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import ProviderCredential
from app.services import provider_credential_service as service


def test_provider_credentials_are_encrypted_and_owner_scoped(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service.save_credentials(db, "owner-a", "OLLAMA", "secret-a", base_url="https://a.example")
        service.save_credentials(db, "owner-b", "ollama", "secret-b")

        row = db.query(ProviderCredential).filter_by(owner_id="owner-a").one()
        assert "secret-a" not in row.api_key_encrypted
        assert service.get_credentials(db, "owner-a", "ollama") == service.ProviderCredentials(
            owner_id="owner-a",
            provider="ollama",
            api_key="secret-a",
            base_url="https://a.example",
        )
        assert service.get_credentials(db, "missing", "ollama") is None
    engine.dispose()


def test_saving_again_updates_the_same_owner_provider(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service.save_credentials(db, "owner", "ollama", "first")
        service.save_credentials(db, "owner", "ollama", "second")

        assert db.query(ProviderCredential).count() == 1
        assert service.get_credentials(db, "owner", "ollama").api_key == "second"
    engine.dispose()
