from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import db as database, gmail_client
from app.config import settings
from app.models import GmailCredential
from app.services import gmail_credential_service as service


def credentials(*, expired=False):
    creds = Credentials(
        token="access-old", refresh_token="refresh-original",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client", client_secret="secret", scopes=gmail_client.SCOPES,
    )
    creds.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=-1 if expired else 1)
    return creds


@pytest.fixture
def storage(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    GmailCredential.__table__.create(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(settings, "feature_db_credentials_enabled", True)
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_token_path", str(tmp_path / "google_token.json"))
    monkeypatch.setattr(gmail_client, "is_gmail_configured", lambda: True)
    monkeypatch.setattr(gmail_client.InstalledAppFlow, "from_client_config", Mock(side_effect=AssertionError("interactive flow")))
    yield engine
    engine.dispose()


def save(owner, creds):
    with database.session_scope() as db:
        service.save_credentials(db, owner, creds)


def test_database_loads_the_selected_owner_and_ignores_the_file(storage):
    save("one", credentials())
    second = credentials()
    second.token = "access-two"
    save("two", second)
    Path(settings.google_token_path).write_text("corrupt", encoding="utf-8")
    assert gmail_client._load_credentials("two").token == "access-two"
    assert gmail_client._load_credentials("one").token == "access-old"


def test_refresh_closes_the_database_connection_then_commits(storage, monkeypatch):
    save("one", credentials(expired=True))
    checked_out = []
    event.listen(storage, "checkout", lambda *args: checked_out.append(True))
    event.listen(storage, "checkin", lambda *args: checked_out.pop())

    def refresh(creds, request):
        assert not checked_out
        creds.token = "access-new"
        creds.expiry = credentials().expiry

    monkeypatch.setattr(Credentials, "refresh", refresh)
    assert gmail_client._load_credentials("one").token == "access-new"
    with database.session_scope() as db:
        row = service.get_row(db, "one")
        assert service.decrypt(row.access_token_encrypted) == "access-new"
        assert service.decrypt(row.refresh_token_encrypted) == "refresh-original"
        assert row.last_refreshed_at is not None


def test_failed_refresh_persists_revocation_without_secret_logging(storage, monkeypatch, caplog):
    save("one", credentials(expired=True))
    monkeypatch.setattr(Credentials, "refresh", Mock(side_effect=RefreshError("secret-sentinel")))
    with pytest.raises(gmail_client.GmailReconnectRequired):
        gmail_client._load_credentials("one")
    with database.session_scope() as db:
        row = service.get_row(db, "one")
        assert row.revoked_at is not None
        assert not row.access_token_encrypted
        assert not row.refresh_token_encrypted
        assert row.last_error == "refresh_failed"
    assert "secret-sentinel" not in caplog.text


def test_default_owner_imports_the_legacy_file_once(storage):
    path = Path(settings.google_token_path)
    path.write_text(credentials().to_json(), encoding="utf-8")
    assert gmail_client._load_credentials().token == "access-old"
    assert not path.exists()
    assert path.with_suffix(".json.imported").exists()
    assert gmail_client._load_credentials().token == "access-old"


def test_another_owner_cannot_adopt_the_legacy_file(storage, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    path = Path(settings.google_token_path)
    path.write_text(credentials().to_json(), encoding="utf-8")
    with pytest.raises(gmail_client.GmailReconnectRequired):
        gmail_client._load_credentials("another-owner")
    assert path.exists()
    with database.session_scope() as db:
        assert service.get_row(db, "another-owner") is None


def test_revoked_owner_cannot_reimport_a_legacy_token(storage, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    save(settings.owner_id, credentials())
    with database.session_scope() as db:
        service.mark_revoked(db, settings.owner_id, "disconnected")
    Path(settings.google_token_path).write_text(credentials().to_json(), encoding="utf-8")
    with pytest.raises(gmail_client.GmailReconnectRequired):
        gmail_client._load_credentials()


def test_pytest_interactive_guard_keeps_its_diagnostic(storage):
    with pytest.raises(RuntimeError, match="needs to be mocked"):
        gmail_client._load_credentials()


def test_flag_off_refreshes_the_legacy_file_without_a_database(storage, monkeypatch):
    monkeypatch.setattr(settings, "feature_db_credentials_enabled", False)
    monkeypatch.setattr(gmail_client, "session_scope", Mock(side_effect=AssertionError("database access")))
    path = Path(settings.google_token_path)
    path.write_text(credentials(expired=True).to_json(), encoding="utf-8")

    def refresh(creds, request):
        creds.token = "legacy-refreshed"
        creds.expiry = credentials().expiry

    monkeypatch.setattr(Credentials, "refresh", refresh)
    assert gmail_client._load_credentials().token == "legacy-refreshed"
    assert Credentials.from_authorized_user_file(str(path)).token == "legacy-refreshed"


def test_session_scope_rolls_back_and_closes_on_error(storage):
    with pytest.raises(ValueError):
        with database.session_scope() as db:
            service.save_credentials(db, "rollback-owner", credentials())
            raise ValueError("rollback")
    with database.session_scope() as db:
        assert service.get_row(db, "rollback-owner") is None


@pytest.mark.parametrize("factory,api,version", [
    (gmail_client._gmail_service, "gmail", "v1"),
    (gmail_client._sheets_service, "sheets", "v4"),
])
def test_service_factories_forward_the_owner(monkeypatch, factory, api, version):
    creds = credentials()
    loader = Mock(return_value=creds)
    builder = Mock()
    monkeypatch.setattr(gmail_client, "_load_credentials", loader)
    monkeypatch.setattr(gmail_client, "build", builder)
    factory("selected-owner")
    loader.assert_called_once_with("selected-owner")
    builder.assert_called_once_with(api, version, credentials=creds)
