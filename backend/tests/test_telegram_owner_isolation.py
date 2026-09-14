import ast
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import TelegramLink
from app.services import telegram_link_service
from app.telegram_bot import TelegramBotService


def _service(*, alerts_enabled=True, authorized=None, chats=None):
    return TelegramBotService(
        token="token",
        alerts_enabled=alerts_enabled,
        is_authorized=authorized or (lambda _chat_id, _text: False),
        chat_ids_for_owner=chats or (lambda _owner_id: []),
        authorized_chat_count=lambda: 0,
        command_handler=lambda _chat_id, _user_id, _username, _text: "ok",
        callback_handler=lambda _chat_id, _user_id, _username, _data, _message_id: "ok",
    )


def test_notify_owner_sends_only_to_that_owners_chat():
    sent: list[int] = []
    service = _service(chats=lambda owner_id: [11] if owner_id == "owner-a" else [22])
    service._send_message = lambda chat_id, _text, **_kwargs: sent.append(chat_id)

    service.notify_owner("owner-a", "private digest")

    assert set(sent) == {11}


def test_global_and_per_owner_alert_switches_suppress_delivery():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([
            TelegramLink(owner_id="owner-a", chat_id=11, alerts_enabled=False),
            TelegramLink(owner_id="owner-b", chat_id=22, alerts_enabled=True),
        ])
        db.commit()

        sent: list[int] = []
        service = _service(chats=lambda owner_id: telegram_link_service.chat_ids_for_owner(db, owner_id))
        service._send_message = lambda chat_id, _text, **_kwargs: sent.append(chat_id)
        service.notify_owner("owner-a", "a")
        service.notify_owner("owner-b", "b")
        assert sent == [22]

        disabled = _service(alerts_enabled=False, chats=lambda _owner_id: [22])
        disabled._send_message = lambda chat_id, _text, **_kwargs: sent.append(chat_id)
        disabled.notify_owner("owner-b", "b")
        assert sent == [22]
    engine.dispose()


def test_unknown_chat_gate_only_allows_private_start_with_argument():
    seen: list[str] = []
    service = _service(authorized=lambda chat_id, text: chat_id == 1 or text.startswith("/start "))
    service._send_message = lambda _chat_id, text, **_kwargs: seen.append(text)

    service._handle_update({"message": {"text": "/menu", "chat": {"id": 2, "type": "private"}, "from": {"id": 2}}})
    service._handle_update({"message": {"text": "/start", "chat": {"id": 2, "type": "private"}, "from": {"id": 2}}})
    service._handle_update({"message": {"text": "/start code", "chat": {"id": 2, "type": "private"}, "from": {"id": 2}}})
    service._handle_update({"message": {"text": "/start code", "chat": {"id": 3, "type": "group"}, "from": {"id": 3}}})

    assert seen == [
        "Unauthorized chat. Access denied.",
        "Unauthorized chat. Access denied.",
        "ok",
        "Only private Telegram chats can be linked.",
    ]


def test_broadcast_notify_method_does_not_exist():
    tree = ast.parse((Path(__file__).parents[1] / "app" / "telegram_bot.py").read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "notify" for node in ast.walk(tree))
