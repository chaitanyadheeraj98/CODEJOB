from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import tenancy
from app.config import settings
from app.db import Base
from app.models import ChatSession, TelegramLink, UserSettings
from app.services import telegram_chat_service, telegram_link_service
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps


def _db():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, future=True)


def _runtime(factory, resolve) -> TelegramRuntime:
    return TelegramRuntime(
        TelegramRuntimeDeps(
            session_factory=factory,
            get_settings=lambda db: db.query(UserSettings).filter_by(owner_id=tenancy.owner_id()).one(),
            read_policy_from_settings=lambda _settings: {},
            policy_dry_run=lambda _policy: False,
            format_query_preflight=lambda _settings, _policy: "",
            poll_interval_minutes=lambda _settings: 10,
            build_telegram_digest=lambda prefix, _result: prefix,
            gmail_auth_status=lambda: (False, False, ""),
            ai_status=lambda: SimpleNamespace(connected=False, model="auto"),
            gmail_sync=lambda _db: None,
            automation_run_once=lambda _payload, _db: None,
            get_candidate_review=lambda _id, _db: None,
            approve_and_send=lambda _id, _payload, _db: None,
            reject_candidate=lambda _id, _payload, _db: None,
            resolve_owner=resolve,
            redeem_link_code=lambda _code, _chat_id, _user_id, _username: None,
            action_lock=__import__("threading").Lock(),
            verify_action_pin=lambda _owner_id, _pin: True,
            auth_ttl_minutes=lambda: 30,
        )
    )


def test_existing_chat_session_default_is_web() -> None:
    engine, factory = _db()
    try:
        with factory() as db:
            row = ChatSession(owner_id="owner-a")
            db.add(row)
            db.flush()
            assert row.origin == "web"
    finally:
        engine.dispose()


def test_current_session_is_stable_and_owner_isolated() -> None:
    engine, factory = _db()
    try:
        with factory() as db:
            db.add_all([
                TelegramLink(owner_id="owner-a", chat_id=11),
                TelegramLink(owner_id="owner-b", chat_id=22),
            ])
            db.flush()
            first = telegram_chat_service.current_session(db, "owner-a", 11)
            again = telegram_chat_service.current_session(db, "owner-a", 11)
            other = telegram_chat_service.current_session(db, "owner-b", 22)
            assert first.id == again.id
            assert first.id != other.id
            assert first.owner_id == "owner-a"
            assert other.owner_id == "owner-b"
    finally:
        engine.dispose()


def test_new_unbinds_without_deleting_previous_session() -> None:
    engine, factory = _db()
    try:
        with factory() as db:
            db.add(TelegramLink(owner_id="owner-a", chat_id=11))
            db.flush()
            first = telegram_chat_service.current_session(db, "owner-a", 11)
            first_id = first.id
            telegram_chat_service.reset_session(db, "owner-a", 11)
            second = telegram_chat_service.current_session(db, "owner-a", 11)
            assert second.id != first_id
            assert db.get(ChatSession, first_id) is not None
    finally:
        engine.dispose()


def test_cross_owner_link_cannot_be_used() -> None:
    engine, factory = _db()
    try:
        with factory() as db:
            db.add(TelegramLink(owner_id="owner-b", chat_id=22))
            db.flush()
            try:
                telegram_chat_service.current_session(db, "owner-a", 22)
            except Exception as exc:
                assert getattr(exc, "status_code", None) == 404
            else:
                raise AssertionError("cross-owner link was accepted")
    finally:
        engine.dispose()


def test_disconnect_unlinks_and_next_message_is_refused() -> None:
    engine, factory = _db()
    with factory() as db:
        db.add_all([
            TelegramLink(owner_id="owner-a", chat_id=11),
            UserSettings(owner_id="owner-a", gmail_query="is:unread", default_gmail_query="is:unread"),
        ])
        db.commit()

    def resolve(chat_id: int):
        with factory() as db:
            return telegram_link_service.resolve_owner(db, chat_id)

    runtime = _runtime(factory, resolve)
    try:
        assert runtime.handle_command(11, "u", "name", "/disconnect") == "Telegram account disconnected."
        assert runtime.handle_command(11, "u", "name", "/status") == "This chat is not linked to an account."
    finally:
        engine.dispose()


def test_new_command_creates_a_fresh_bound_session() -> None:
    engine, factory = _db()
    with factory() as db:
        db.add_all([
            TelegramLink(owner_id="owner-a", chat_id=11),
            UserSettings(owner_id="owner-a", gmail_query="is:unread", default_gmail_query="is:unread"),
        ])
        first = telegram_chat_service.current_session(db, "owner-a", 11)
        first_id = first.id
        db.commit()
    runtime = _runtime(factory, lambda _chat_id: "owner-a")
    previous = settings.feature_telegram_chat_enabled
    settings.feature_telegram_chat_enabled = True
    try:
        assert runtime.handle_command(11, "u", "name", "/new") == "Started a new assistant conversation."
        with factory() as db:
            second = telegram_chat_service.current_session(db, "owner-a", 11)
            assert second.id != first_id
            assert db.get(ChatSession, first_id) is not None
    finally:
        settings.feature_telegram_chat_enabled = previous
        engine.dispose()
