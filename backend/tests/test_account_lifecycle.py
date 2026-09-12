import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as database, main, tenancy
from app.config import settings
from app.db import Base
from app.models import GmailCredential, ProviderCredential, RecentRun, User, UserSession
from app.services import account_service, auth_service


class TestAccountDeactivation:
    def setup_method(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)
        self.previous = (
            settings.feature_auth_enabled,
            settings.session_cookie_secure,
            database.SessionLocal,
            main.SessionLocal,
        )
        settings.feature_auth_enabled = True
        settings.session_cookie_secure = False
        database.SessionLocal = self.SessionLocal
        main.SessionLocal = self.SessionLocal
        self.token = "raw-session-token"
        with Session(self.engine) as db:
            user = User(owner_id="usr_owner", email="owner@example.com")
            other = User(owner_id="usr_other", email="other@example.com")
            db.add_all([user, other])
            db.flush()
            db.add_all([
                UserSession(
                    user_id=user.id,
                    token_hash=auth_service.hash_session_token(self.token),
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
                GmailCredential(
                    owner_id=user.owner_id,
                    access_token_encrypted="ciphertext",
                    refresh_token_encrypted="ciphertext",
                ),
                ProviderCredential(owner_id=user.owner_id, provider="ollama", api_key_encrypted="ciphertext"),
                GmailCredential(owner_id=other.owner_id, access_token_encrypted="other"),
                RecentRun(
                    owner_id=user.owner_id,
                    run_source="gmail_sync",
                    run_key="gmail_sync:active",
                    status="queued",
                ),
            ])
            db.commit()
        self.client.cookies.set(settings.session_cookie_name, self.token)

    def teardown_method(self):
        (
            settings.feature_auth_enabled,
            settings.session_cookie_secure,
            database.SessionLocal,
            main.SessionLocal,
        ) = self.previous
        main.app.dependency_overrides.clear()
        self.client.close()
        self.engine.dispose()

    def _deactivate(self):
        credentials = SimpleNamespace(refresh_token="refresh-secret", token="access-secret")
        with (
            patch.object(account_service.gmail_credential_service, "get_credentials", return_value=credentials),
            patch.object(account_service, "revoke_google_token") as revoke,
            patch.object(account_service, "get_redis_connection", return_value=Mock()),
        ):
            response = self.client.post("/account/deactivate")
        return response, revoke

    def test_deactivation_revokes_access_and_starts_the_30_day_window(self):
        response, revoke = self._deactivate()

        assert response.status_code == 200, response.text
        payload = response.json()
        deactivated_at = datetime.fromisoformat(payload["deactivated_at"])
        purge_after = datetime.fromisoformat(payload["purge_after"])
        assert purge_after - deactivated_at == timedelta(days=30)
        assert payload["sessions_revoked"] == 1
        assert payload["jobs_stopped"] == 1
        revoke.assert_called_once_with("refresh-secret")

        with Session(self.engine) as db:
            user = db.query(User).filter(User.owner_id == "usr_owner").one()
            assert user.disabled_at is not None
            assert user.deletion_requested_at is not None
            assert db.query(UserSession).filter(UserSession.user_id == user.id).one().revoked_at is not None
            assert db.query(GmailCredential).filter(GmailCredential.owner_id == "usr_owner").count() == 0
            assert db.query(ProviderCredential).filter(ProviderCredential.owner_id == "usr_owner").count() == 0
            assert db.query(GmailCredential).filter(GmailCredential.owner_id == "usr_other").count() == 1
            run = db.query(RecentRun).filter(RecentRun.owner_id == "usr_owner").one()
            assert run.status == "canceled"
        assert "Max-Age=0" in response.headers["set-cookie"]

    def test_google_failure_does_not_leave_local_credentials_or_a_live_session(self):
        credentials = SimpleNamespace(refresh_token="refresh-secret", token="")
        with (
            patch.object(account_service.gmail_credential_service, "get_credentials", return_value=credentials),
            patch.object(account_service, "revoke_google_token", side_effect=OSError("offline")),
            patch.object(account_service, "get_redis_connection", return_value=Mock()),
        ):
            response = self.client.post("/account/deactivate")

        assert response.status_code == 200
        with Session(self.engine) as db:
            assert db.query(GmailCredential).filter(GmailCredential.owner_id == "usr_owner").count() == 0
            assert db.query(ProviderCredential).filter(ProviderCredential.owner_id == "usr_owner").count() == 0
            assert db.query(UserSession).filter(UserSession.revoked_at.is_(None)).count() == 0

    def test_anonymous_and_auth_disabled_requests_are_refused(self):
        self.client.cookies.clear()
        assert self.client.post("/account/deactivate").status_code == 401
        settings.feature_auth_enabled = False
        assert self.client.post("/account/deactivate").status_code == 404

    def test_a_queued_job_for_a_disabled_owner_is_skipped_before_its_body(self):
        with Session(self.engine) as db:
            db.query(User).filter(User.owner_id == "usr_owner").one().disabled_at = datetime.now(UTC)
            db.commit()
        body = Mock(return_value={"status": "ok"})
        job = tenancy.owner_scoped(body)

        result = job(owner_id="usr_owner")

        assert result == {"status": "skipped", "reason": "account_disabled"}
        body.assert_not_called()
