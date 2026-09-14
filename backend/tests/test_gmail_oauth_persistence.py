"""A6 — a completed OAuth flow lands in the database, not the file.

The flag-off path must keep writing the file byte-for-byte as before: that is
the revert route for the whole of phase A, and it is only a revert route if it
still works.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet
from google.oauth2.credentials import Credentials
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db as database, gmail_client
from app.config import settings
from app.models import GmailCredential
from app.services import gmail_credential_service as service


def _credentials(token: str = "access-new", refresh: str = "refresh-new") -> Credentials:
    creds = Credentials(
        token=token,
        refresh_token=refresh,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client",
        client_secret="secret",
        scopes=gmail_client.SCOPES,
    )
    creds.expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1)
    return creds


@pytest.fixture
def storage(monkeypatch, tmp_path):
    engine = create_engine("sqlite://")
    GmailCredential.__table__.create(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "google_token_path", str(tmp_path / "google_token.json"))
    monkeypatch.setattr(settings, "feature_db_credentials_enabled", True)
    yield engine
    engine.dispose()


def _stub_profile(monkeypatch, email: str = "owner@example.com") -> None:
    monkeypatch.setattr(gmail_client, "_profile_email", lambda creds: email)


def test_a_completed_flow_is_stored_encrypted_in_the_database(storage, monkeypatch):
    _stub_profile(monkeypatch)

    gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert service.decrypt(row.refresh_token_encrypted) == "refresh-new"
        assert row.refresh_token_encrypted != "refresh-new"
        assert row.google_email == "owner@example.com"


def test_the_token_file_is_not_written_when_the_flag_is_on(storage, monkeypatch):
    _stub_profile(monkeypatch)

    gmail_client._persist_new_credentials(_credentials())

    assert not Path(settings.google_token_path).exists()


def test_the_flag_off_path_still_writes_the_file_and_touches_no_row(storage, monkeypatch):
    """The revert route for all of phase A. It has to keep working."""
    monkeypatch.setattr(settings, "feature_db_credentials_enabled", False)

    gmail_client._persist_new_credentials(_credentials())

    assert Path(settings.google_token_path).exists()
    with database.session_scope() as db:
        assert db.query(GmailCredential).count() == 0


def test_a_profile_lookup_failure_does_not_lose_the_connection(storage, monkeypatch):
    """A transient getProfile error must not cost a consent the user just gave."""
    monkeypatch.setattr(
        gmail_client, "build", Mock(side_effect=RuntimeError("network"))
    )

    gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert service.decrypt(row.refresh_token_encrypted) == "refresh-new"
        assert row.google_email == "", "backfilled on the next successful save"


def test_reconnecting_does_not_wipe_the_stored_refresh_token(storage, monkeypatch):
    """The weekly re-consent case, end to end through the OAuth path."""
    _stub_profile(monkeypatch)
    gmail_client._persist_new_credentials(_credentials(refresh="refresh-original"))

    reconsent = _credentials(token="access-second", refresh=None)
    gmail_client._persist_new_credentials(reconsent)

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert service.decrypt(row.refresh_token_encrypted) == "refresh-original"
        assert service.decrypt(row.access_token_encrypted) == "access-second"


def test_a_reconnect_clears_a_previous_revocation(storage, monkeypatch):
    _stub_profile(monkeypatch)
    gmail_client._persist_new_credentials(_credentials())
    with database.session_scope() as db:
        service.mark_revoked(db, settings.owner_id, "refresh_failed")

    gmail_client._persist_new_credentials(_credentials(refresh="refresh-again"))

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert row.revoked_at is None
        assert row.last_error == ""


def test_the_state_parameter_is_still_passed_to_the_local_server(storage, monkeypatch):
    """CSRF defence. Preserving it was an explicit requirement of the rewrite.

    Takes `storage` purely for isolation: without it `_persist_new_credentials`
    falls through to the real `settings.google_token_path` and writes fake
    credentials into `backend/data/`. It did exactly that once.
    """
    _stub_profile(monkeypatch)
    flow = Mock()
    flow.run_local_server.return_value = _credentials()

    gmail_client._run_prepared_oauth_flow(flow, "the-state", {"prompt": "select_account"})

    assert flow.run_local_server.call_args.kwargs["state"] == "the-state"


# -- B2: identity verification at the point of persistence -------------------


def _identity(email: str = "owner@example.com", subject: str = "sub-1"):
    from app.services.google_identity_service import VerifiedIdentity

    return VerifiedIdentity(subject=subject, email=email, email_verified=True)


def test_the_verified_subject_and_email_are_stored(storage, monkeypatch):
    """google_subject was nullable until the identity scopes arrived."""
    _stub_profile(monkeypatch, "owner@example.com")
    monkeypatch.setattr(gmail_client, "verified_identity", lambda creds: _identity())

    gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert row.google_subject == "sub-1"
        assert row.google_email == "owner@example.com"


def test_a_mailbox_that_is_not_the_signed_in_account_is_refused(storage, monkeypatch):
    """The check that stops one account attaching another's mailbox."""
    from app.services.google_identity_service import IdentityVerificationError

    _stub_profile(monkeypatch, "someone.else@example.com")
    monkeypatch.setattr(gmail_client, "verified_identity", lambda creds: _identity())

    with pytest.raises(IdentityVerificationError):
        gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        assert db.query(GmailCredential).count() == 0, "nothing is stored on a mismatch"


def test_a_credential_with_no_id_token_still_connects(storage, monkeypatch):
    """Anything issued before the identity scopes must keep working."""
    _stub_profile(monkeypatch)
    monkeypatch.setattr(gmail_client, "verified_identity", lambda creds: None)

    gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        row = db.query(GmailCredential).one()
        assert row.google_subject is None
        assert row.google_email == "owner@example.com"


def test_the_identity_email_wins_over_the_profile_lookup(storage, monkeypatch):
    """Both name the same account by then; the verified one is the evidence."""
    _stub_profile(monkeypatch, "Owner@Example.com")
    monkeypatch.setattr(gmail_client, "verified_identity", lambda creds: _identity("owner@example.com"))

    gmail_client._persist_new_credentials(_credentials())

    with database.session_scope() as db:
        assert db.query(GmailCredential).one().google_email == "owner@example.com"


def test_identity_scopes_are_requested():
    assert "openid" in gmail_client.SCOPES
    assert "https://www.googleapis.com/auth/userinfo.email" in gmail_client.SCOPES


def test_oauthlib_scope_relaxation_is_set():
    """Google reorders the scope list once openid is in it; oauthlib calls that tampering."""
    import os

    assert os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE") == "1"
