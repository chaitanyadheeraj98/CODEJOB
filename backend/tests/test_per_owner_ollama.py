import asyncio
from contextlib import contextmanager
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import tenancy
from app.ai.chat import llm
from app.ai.chat.failures import FAILURE_MESSAGES, classify_ollama_error
from app.config import settings
from app.db import Base
from app.routers import chat
from app.services import provider_credential_service as credentials


def _database(monkeypatch):
    monkeypatch.setattr(settings, "credential_encryption_key", Fernet.generate_key().decode())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_chat_llm_uses_the_callers_key_and_base_url(monkeypatch):
    engine, db = _database(monkeypatch)
    credentials.save_credentials(db, "owner-a", "ollama", "key-a")
    credentials.save_credentials(
        db, "owner-b", "ollama", "key-b", base_url="https://b.example/"
    )

    @contextmanager
    def scope():
        yield db

    with patch.object(llm, "session_scope", scope), patch.object(llm, "ChatOllama") as client:
        with tenancy.owner_scope("owner-a"):
            llm.build_chat_llm("model-a", 4)
        first = client.call_args.kwargs
        with tenancy.owner_scope("owner-b"):
            llm.build_chat_llm("model-b")
        second = client.call_args.kwargs

    assert first["base_url"] == "https://ollama.com"
    assert first["client_kwargs"]["headers"] == {"Authorization": "Bearer key-a"}
    assert first["client_kwargs"]["timeout"] == 4
    assert second["base_url"] == "https://b.example"
    assert second["client_kwargs"]["headers"] == {"Authorization": "Bearer key-b"}
    db.close()
    engine.dispose()


def test_missing_key_stops_before_constructing_a_client(monkeypatch):
    engine, db = _database(monkeypatch)

    @contextmanager
    def scope():
        yield db

    with patch.object(llm, "session_scope", scope), patch.object(llm, "ChatOllama") as client:
        with tenancy.owner_scope("missing"):
            try:
                llm.build_chat_llm()
            except credentials.ProviderCredentialsUnavailable as exc:
                assert classify_ollama_error(exc) == "ollama_credentials_missing"
                assert "Settings" in FAILURE_MESSAGES["ollama_credentials_missing"]
            else:
                raise AssertionError("a missing owner key must make chat unavailable")
    client.assert_not_called()
    db.close()
    engine.dispose()


def test_status_probe_uses_the_same_owner_key_and_url(monkeypatch):
    engine, db = _database(monkeypatch)
    credentials.save_credentials(
        db, "owner", "ollama", "owner-key", base_url="https://owner.example/"
    )
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return Response()

    with tenancy.owner_scope("owner"), patch.object(chat.httpx, "AsyncClient", Client):
        assert asyncio.run(chat._ollama_running(db))

    assert captured["url"] == "https://owner.example/api/tags"
    assert captured["request"]["headers"] == {"Authorization": "Bearer owner-key"}
    db.close()
    engine.dispose()


def test_status_probe_does_not_fall_back_to_a_shared_key(monkeypatch):
    engine, db = _database(monkeypatch)
    with tenancy.owner_scope("missing"), patch.object(chat.httpx, "AsyncClient") as client:
        assert not asyncio.run(chat._ollama_running(db))
    client.assert_not_called()
    assert "Settings" in (chat.runtime_state.ollama_last_error or "")
    db.close()
    engine.dispose()
