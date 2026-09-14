import threading
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import tenancy
from app.config import settings
from app.db import Base
from app.models import RecruiterEmail, User
from app.services import telegram_link_service
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.telegram_bot import TelegramReply


def _candidate(owner_id: str, suffix: str) -> RecruiterEmail:
    return RecruiterEmail(
        owner_id=owner_id,
        sender=f"{suffix}@example.com",
        subject=f"Candidate {suffix}",
        body="body",
        state="needs_review",
    )


def _runtime(session_factory, owners: dict[int, str]) -> TelegramRuntime:
    def resolve(chat_id: int) -> str | None:
        with session_factory() as db:
            return telegram_link_service.resolve_owner(db, chat_id) or owners.get(chat_id)

    def redeem(code: str, chat_id: int, user_id: str, username: str) -> str | None:
        with session_factory() as db:
            owner = telegram_link_service.redeem_link_code(
                db, code, chat_id=chat_id, telegram_user_id=user_id, username=username
            )
            if owner is not None:
                db.commit()
            return owner

    def verify(owner_id: str, pin: str) -> bool:
        with session_factory() as db:
            return telegram_link_service.verify_action_pin(db, owner_id, pin)

    return TelegramRuntime(
        TelegramRuntimeDeps(
            session_factory=session_factory,
            get_settings=lambda _db: None,
            read_policy_from_settings=lambda _settings: {},
            policy_dry_run=lambda _policy: False,
            format_query_preflight=lambda _settings, _policy: "preflight",
            poll_interval_minutes=lambda _settings: 10,
            build_telegram_digest=lambda prefix, _result: prefix,
            gmail_auth_status=lambda: (False, False, "not configured"),
            ai_status=lambda: None,
            gmail_sync=lambda _db: None,
            automation_run_once=lambda _payload, _db: None,
            get_candidate_review=lambda _email_id, _db: None,
            approve_and_send=lambda _email_id, _payload, _db: None,
            reject_candidate=lambda _email_id, _payload, _db: None,
            resolve_owner=resolve,
            redeem_link_code=redeem,
            action_lock=threading.Lock(),
            verify_action_pin=verify,
            auth_ttl_minutes=lambda: 30,
        )
    )


def test_two_chats_only_see_their_owners_candidates():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add_all([_candidate("owner-a", "a1"), _candidate("owner-a", "a2"), _candidate("owner-b", "b1")])
        db.commit()
    runtime = _runtime(session_factory, {10: "owner-a", 20: "owner-b"})

    owner_a = str(runtime.handle_command(10, "10", "a", "/needs_review"))
    owner_b = str(runtime.handle_command(20, "20", "b", "/needs_review"))
    assert "Needs Review: 2" in owner_a
    assert "Candidate a1" in owner_a
    assert "Candidate b1" not in owner_a
    assert "Needs Review: 1" in owner_b
    assert "Candidate b1" in owner_b
    assert "Candidate a1" not in owner_b
    assert tenancy.current_owner_id_or_none() is None
    engine.dispose()


def test_unlinked_chat_is_prompted_and_valid_start_code_links_it():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    runtime = _runtime(session_factory, {})

    assert runtime.handle_command(30, "30", "new", "/start") == (
        "Open Settings -> Integrations to link this Telegram account."
    )
    assert runtime.handle_command(30, "30", "new", "/needs_review") == "This chat is not linked to an account."
    with session_factory() as db:
        code = telegram_link_service.issue_link_code(db, "owner-c")
        db.commit()

    reply = runtime.handle_command(30, "30", "new", f"/start {code}")
    assert isinstance(reply, TelegramReply)
    assert reply.text.startswith("Telegram account linked.")
    with session_factory() as db:
        assert telegram_link_service.resolve_owner(db, 30) == "owner-c"
    assert tenancy.current_owner_id_or_none() is None
    engine.dispose()


def test_deactivated_check_uses_resolved_owner():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    with session_factory() as db:
        db.add(User(owner_id="owner-b", email="disabled@example.com", disabled_at=datetime.now(UTC)))
        db.commit()
    runtime = _runtime(session_factory, {20: "owner-b"})
    previous = settings.feature_auth_enabled
    settings.feature_auth_enabled = True
    try:
        assert runtime.handle_command(20, "20", "b", "/needs_review") == "Account is deactivated."
    finally:
        settings.feature_auth_enabled = previous
    assert tenancy.current_owner_id_or_none() is None
    engine.dispose()
