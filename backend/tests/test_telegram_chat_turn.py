import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, tenancy
from app.config import settings
from app.db import Base
from app.jobs import tasks
from app.models import ChatMessage, ChatSession, TelegramLink, UserSettings
from app.runtime_state import runtime_state
from app.services.chat_service import ChatService, ChatTurnResult
from app.services.telegram_runtime_service import TelegramRuntime
from app.telegram_bot import TelegramBotService, TelegramReply


def _bot(enqueued: list[tuple[int, int, str]]) -> TelegramBotService:
    service = TelegramBotService(
        token="token",
        alerts_enabled=True,
        is_authorized=lambda _chat_id, _text: True,
        chat_ids_for_owner=lambda _owner: [],
        authorized_chat_count=lambda: 1,
        command_handler=lambda _chat_id, _user, _name, text: TelegramReply(
            text="Thinking…", enqueue_chat_text=text
        ),
        callback_handler=lambda *_args: "ok",
        chat_turn_handler=lambda chat_id, message_id, text: enqueued.append((chat_id, message_id, text)),
    )
    next_id = iter(range(100, 110))
    service.transport._post_json = lambda _method, _payload: {
        "ok": True,
        "result": {"message_id": next(next_id)},
    }
    return service


def test_poller_hands_off_without_waiting_for_the_turn_and_advances_offset() -> None:
    enqueued: list[tuple[int, int, str]] = []
    service = _bot(enqueued)
    update = {"update_id": 7, "message": {"text": "slow question", "chat": {"id": 11}, "from": {"id": 2}}}
    service._poll_updates = lambda: [update]  # type: ignore[method-assign]
    service._running = True
    service._chat_turn_handler = lambda chat_id, message_id, text: (
        enqueued.append((chat_id, message_id, text)),
        setattr(service, "_running", False),
    )

    async def slow_turn() -> None:
        await asyncio.sleep(2)

    pending_turn = slow_turn()
    try:
        started = __import__("time").perf_counter()
        service._run_loop()
        elapsed = __import__("time").perf_counter() - started
    finally:
        pending_turn.close()

    assert elapsed < 0.5
    assert service._offset == 8
    assert enqueued == [(11, 100, "slow question")]


def test_repeated_free_text_is_not_deduplicated() -> None:
    enqueued: list[tuple[int, int, str]] = []
    service = _bot(enqueued)
    update = {"message": {"text": "again", "chat": {"id": 11}, "from": {"id": 2}}}
    service._handle_update(update)
    service._handle_update(update)
    assert [item[2] for item in enqueued] == ["again", "again"]


def test_pending_input_wins_over_free_text_chat() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as db:
        db.add_all([
            TelegramLink(owner_id="owner-a", chat_id=11),
            UserSettings(owner_id="owner-a", gmail_query="old", default_gmail_query="old"),
        ])
        db.commit()
    runtime = TelegramRuntime(
        SimpleNamespace(
            resolve_owner=lambda _chat_id: "owner-a",
            session_factory=factory,
            get_settings=lambda db: db.query(UserSettings).filter_by(owner_id="owner-a").one(),
        )
    )
    previous = settings.feature_telegram_chat_enabled
    settings.feature_telegram_chat_enabled = True
    runtime_state.telegram_pending_inputs[11] = "await_setquery"
    try:
        reply = runtime.handle_command(11, "u", "name", "after:2026/09/01")
        assert reply == "Query updated to: after:2026/09/01"
        with factory() as db:
            assert db.query(UserSettings).filter_by(owner_id="owner-a").one().gmail_query == "after:2026/09/01"
    finally:
        settings.feature_telegram_chat_enabled = previous
        runtime_state.telegram_pending_inputs.pop(11, None)
        engine.dispose()


def test_complete_message_drains_internal_stream_and_returns_persisted_rows() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    service = ChatService()

    @asynccontextmanager
    async def lease(*_args):
        yield

    async def send(db, session_id, *_args):
        db.add(ChatMessage(session_id=session_id, role="tool", content='{"ok":true}', tool_name="get_status"))
        db.add(ChatMessage(session_id=session_id, role="assistant", content="Final answer"))
        db.commit()
        yield "not-an-sse-contract"

    service._admission_lease = lease  # type: ignore[method-assign]
    service._send_message = send  # type: ignore[method-assign]
    try:
        with factory() as db, tenancy.owner_scope("owner-a"):
            session = ChatSession(owner_id="owner-a")
            db.add(session)
            db.commit()
            result = asyncio.run(service.complete_message(db, session.id, "question"))
            assert result.assistant_text == "Final answer"
            assert [row.tool_name for row in result.tool_rows] == ["get_status"]
    finally:
        engine.dispose()


class _FakeDb:
    def close(self) -> None:
        pass


def _run_worker(monkeypatch, outcome, *, owner: str = "owner-a"):
    seen: dict[str, object] = {}

    class Service:
        async def complete_message(self, _db, session_id, text, model=None):
            seen["owner_in_coroutine"] = tenancy.owner_id()
            seen["session_id"] = session_id
            seen["text"] = text
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

    class Transport:
        def __init__(self, _token):
            pass

        def edit_message(self, chat_id, message_id, text):
            seen["edit"] = (chat_id, message_id, text)

        def send_message(self, chat_id, text, *, inline_keyboard=None):
            seen.setdefault("sent", []).append((chat_id, text, inline_keyboard))

    monkeypatch.setattr(tasks, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr("app.services.chat_service.ChatService", Service)
    monkeypatch.setattr(
        "app.services.telegram_chat_service.current_session",
        lambda _db, current_owner, chat_id: seen.update({"current_owner": current_owner, "chat_id": chat_id}) or SimpleNamespace(id=31),
    )
    monkeypatch.setattr("app.telegram_bot.TelegramTransport", Transport)
    result = tasks.run_telegram_chat_turn(chat_id=11, message_id=22, text="private text", owner_id=owner)
    return result, seen


def test_worker_owner_scope_is_present_inside_asyncio_task(monkeypatch) -> None:
    _result, seen = _run_worker(monkeypatch, ChatTurnResult("answer", []), owner="owner-b")
    assert seen["owner_in_coroutine"] == "owner-b"
    assert seen["current_owner"] == "owner-b"
    assert seen["edit"] == (11, 22, "answer")


def test_worker_sends_email_proposal_card_from_persisted_tool_row(monkeypatch) -> None:
    payload = {
        "action": "send_email",
        "candidate_email_id": 7,
        "to": "recruiter@example.com",
        "subject": "Role",
        "body": "Hello",
        "document_ids": [],
        "document_names": [],
    }
    tool = SimpleNamespace(id=91, tool_name="propose_send_email", content=__import__("json").dumps(payload))
    _result, seen = _run_worker(monkeypatch, ChatTurnResult("answer", [tool]))
    assert seen["sent"][0][2][0][0]["callback_data"] == "act:prop:send:91"


def test_worker_keeps_answer_when_chart_has_no_provenance(monkeypatch) -> None:
    payload = {
        "action": "render_chart",
        "chart_type": "activity_trend",
        "title": "Approved sends",
        "max_value": 1,
        "series": [{"label": "Today", "value": 1}],
    }
    tool = SimpleNamespace(id=92, tool_name="get_chart", content=__import__("json").dumps(payload))
    _result, seen = _run_worker(monkeypatch, ChatTurnResult("Owner answer", [tool]))
    assert seen["edit"] == (11, 22, "Owner answer")
    assert seen["sent"][0][1] == "Chart omitted: source provenance was missing."


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (HTTPException(status_code=503, detail="busy"), "I'm at capacity right now"),
        (HTTPException(status_code=422, detail="long"), "That message is too long"),
        (TimeoutError(), "That took too long"),
        (ChatTurnResult("partial", [], "budget_exhausted"), "That took too long"),
        (RuntimeError("private text"), "Something went wrong on my side. Reference:"),
    ],
)
def test_worker_failure_messages(monkeypatch, outcome, expected) -> None:
    _result, seen = _run_worker(monkeypatch, outcome)
    assert expected in seen["edit"][2]
    assert "private text" not in seen["edit"][2]


def test_enqueue_uses_owner_and_content_free_job_id(monkeypatch) -> None:
    calls: list[dict] = []
    queue = SimpleNamespace(enqueue=lambda _task, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(main, "_resolve_telegram_owner", lambda _chat_id: "owner-a")
    monkeypatch.setattr(main, "get_queue", lambda name: calls.append({"queue": name}) or queue)
    main._enqueue_telegram_chat_turn(11, 22, "sensitive question")
    assert calls[0] == {"queue": "telegram_chat"}
    assert calls[1]["kwargs"]["owner_id"] == "owner-a"
    assert calls[1]["kwargs"]["text"] == "sensitive question"
    assert calls[1]["job_id"].startswith("telegram-chat-")
    assert "sensitive" not in calls[1]["job_id"]
