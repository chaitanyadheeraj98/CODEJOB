from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.config import settings
from app.db import Base
from app.runtime_state import runtime_state
from app.services import telegram_link_service
from app.telegram_bot import TelegramBotService


def test_bot_username_is_loaded_once_when_service_starts():
    service = TelegramBotService(
        token="token",
        alerts_enabled=True,
        is_authorized=lambda _chat_id, _text: False,
        chat_ids_for_owner=lambda _owner_id: [],
        authorized_chat_count=lambda: 0,
        command_handler=lambda _chat_id, _user_id, _username, _text: "ok",
        callback_handler=lambda _chat_id, _user_id, _username, _data, _message_id: "ok",
    )
    calls: list[str] = []
    service.transport._post_json = lambda method, _payload: calls.append(method) or {
        "ok": True,
        "result": {"username": "codejob_bot"},
    }
    service._run_loop = lambda: None

    service.start()
    service.stop()

    assert service.bot_username == "codejob_bot"
    assert calls == ["getMe"]


def test_link_api_is_owner_scoped_rotates_codes_and_returns_no_secrets():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)

    def override_get_db():
        with session_factory() as db:
            yield db

    previous_owner = settings.owner_id
    previous_token = settings.telegram_bot_token
    previous_service = runtime_state.telegram_service
    main.app.dependency_overrides[main.get_db] = override_get_db
    runtime_state.telegram_service = SimpleNamespace(bot_username="codejob_bot")
    settings.telegram_bot_token = "secret-bot-token"
    client = TestClient(main.app)
    try:
        settings.owner_id = "owner-a"
        first = client.post("/telegram/link/code")
        second = client.post("/telegram/link/code")
        assert first.status_code == 200
        assert second.status_code == 200
        first_code = parse_qs(urlparse(first.json()["deep_link"]).query)["start"][0]
        second_code = parse_qs(urlparse(second.json()["deep_link"]).query)["start"][0]
        assert first_code != second_code
        with session_factory() as db:
            assert telegram_link_service.redeem_link_code(
                db, first_code, chat_id=12344821, telegram_user_id="1", username="alice"
            ) is None
            assert telegram_link_service.redeem_link_code(
                db, second_code, chat_id=12344821, telegram_user_id="1", username="alice"
            ) == "owner-a"
            db.commit()

        linked = client.get("/telegram/link")
        assert linked.json()["chat_masked"] == "…4821"
        assert linked.json()["telegram_username"] == "alice"

        pin_response = client.put("/telegram/link/settings", json={"alerts_enabled": False, "action_pin": "1234"})
        serialized = first.text + second.text + linked.text + pin_response.text
        assert pin_response.json()["pin_set"] is True
        assert pin_response.json()["alerts_enabled"] is False
        assert "secret-bot-token" not in serialized
        assert "1234" not in serialized
        assert "action_pin_hash" not in serialized

        settings.owner_id = "owner-b"
        assert client.get("/telegram/link").json()["linked"] is False
        assert client.delete("/telegram/link").json() == {"linked": False}

        settings.owner_id = "owner-a"
        assert client.get("/telegram/link").json()["linked"] is True
        assert client.delete("/telegram/link").json() == {"linked": False}
        assert client.get("/telegram/link").json()["linked"] is False
    finally:
        client.close()
        main.app.dependency_overrides.clear()
        settings.owner_id = previous_owner
        settings.telegram_bot_token = previous_token
        runtime_state.telegram_service = previous_service
        engine.dispose()


def test_link_code_is_503_when_bot_username_is_unknown():
    previous = runtime_state.telegram_service
    runtime_state.telegram_service = None
    client = TestClient(main.app)
    try:
        response = client.post("/telegram/link/code")
        assert response.status_code == 503
        assert "not running" in response.json()["detail"]
    finally:
        client.close()
        runtime_state.telegram_service = previous
