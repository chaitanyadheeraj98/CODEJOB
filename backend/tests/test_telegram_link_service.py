from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import TelegramLink
from app.runtime_state import runtime_state
from app.services import telegram_link_service as service


def _db() -> tuple[object, Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_issue_redeem_and_resolve_without_storing_plaintext():
    engine, db = _db()
    code = service.issue_link_code(db, "owner-a")
    row = db.query(TelegramLink).one()
    assert code not in str(row.__dict__)

    assert service.redeem_link_code(
        db, code, chat_id=2**31 + 9, telegram_user_id="user-1", username="alice"
    ) == "owner-a"
    assert service.resolve_owner(db, 2**31 + 9) == "owner-a"
    assert service.redeem_link_code(
        db, code, chat_id=2**31 + 9, telegram_user_id="user-1", username="alice"
    ) is None
    db.close()
    engine.dispose()


def test_expired_and_unknown_codes_are_refused(monkeypatch):
    engine, db = _db()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(service, "utc_now", lambda: now)
    code = service.issue_link_code(db, "owner-a")
    monkeypatch.setattr(service, "utc_now", lambda: now + timedelta(minutes=11))

    assert service.redeem_link_code(
        db, code, chat_id=1, telegram_user_id="1", username="alice"
    ) is None
    assert service.redeem_link_code(
        db, "unknown", chat_id=1, telegram_user_id="1", username="alice"
    ) is None
    db.close()
    engine.dispose()


def test_reminting_invalidates_the_previous_code():
    engine, db = _db()
    old = service.issue_link_code(db, "owner-a")
    new = service.issue_link_code(db, "owner-a")

    assert old != new
    assert service.redeem_link_code(db, old, chat_id=1, telegram_user_id="1", username="a") is None
    assert service.redeem_link_code(db, new, chat_id=1, telegram_user_id="1", username="a") == "owner-a"
    db.close()
    engine.dispose()


def test_chat_linked_to_another_owner_cannot_be_stolen():
    engine, db = _db()
    owner_b_code = service.issue_link_code(db, "owner-b")
    assert service.redeem_link_code(
        db, owner_b_code, chat_id=77, telegram_user_id="77", username="b"
    ) == "owner-b"
    owner_a_code = service.issue_link_code(db, "owner-a")

    assert service.redeem_link_code(
        db, owner_a_code, chat_id=77, telegram_user_id="77", username="a"
    ) is None
    assert service.resolve_owner(db, 77) == "owner-b"
    db.close()
    engine.dispose()


def test_relink_and_unlink_clear_chat_runtime_state():
    engine, db = _db()
    first = service.issue_link_code(db, "owner-a")
    service.redeem_link_code(db, first, chat_id=1, telegram_user_id="1", username="a")
    runtime_state.telegram_auth_sessions[1] = datetime.now(UTC) + timedelta(minutes=5)
    runtime_state.telegram_pending_inputs[1] = "await_auth_pin"

    second = service.issue_link_code(db, "owner-a")
    service.redeem_link_code(db, second, chat_id=2, telegram_user_id="2", username="a")
    assert 1 not in runtime_state.telegram_auth_sessions
    assert 1 not in runtime_state.telegram_pending_inputs

    runtime_state.telegram_auth_sessions[2] = datetime.now(UTC) + timedelta(minutes=5)
    runtime_state.telegram_pending_inputs[2] = "await_auth_pin"
    assert service.unlink(db, "owner-a")
    assert service.resolve_owner(db, 2) is None
    assert 2 not in runtime_state.telegram_auth_sessions
    assert 2 not in runtime_state.telegram_pending_inputs
    db.close()
    engine.dispose()


def test_pin_set_verify_wrong_and_clear():
    engine, db = _db()
    service.set_action_pin(db, "owner-a", "1234")
    row = db.query(TelegramLink).one()
    assert row.action_pin_hash != "1234"
    assert service.verify_action_pin(db, "owner-a", "1234")
    assert not service.verify_action_pin(db, "owner-a", "9999")

    service.set_action_pin(db, "owner-a", "")
    assert service.verify_action_pin(db, "owner-a", "anything")
    db.close()
    engine.dispose()
