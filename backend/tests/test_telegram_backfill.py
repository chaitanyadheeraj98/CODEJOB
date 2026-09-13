from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import TelegramLink
from app.services import telegram_link_service


def _database():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def test_empty_table_backfills_the_first_legacy_chat_and_pin():
    engine, db = _database()

    assert telegram_link_service.backfill_legacy_link(db, "default-owner", "101,202", "1234") == 1
    db.commit()

    row = db.query(TelegramLink).one()
    assert row.owner_id == "default-owner"
    assert row.chat_id == 101
    assert telegram_link_service.verify_action_pin(db, "default-owner", "1234")
    assert row.action_pin_hash != "1234"
    db.close()
    engine.dispose()


def test_nonempty_table_is_unchanged_across_repeated_backfills():
    engine, db = _database()
    created_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.add(TelegramLink(owner_id="existing", chat_id=9, created_at=created_at))
    db.commit()

    assert telegram_link_service.backfill_legacy_link(db, "default-owner", "101", "1234") == 0
    assert telegram_link_service.backfill_legacy_link(db, "default-owner", "101", "1234") == 0
    db.commit()

    row = db.query(TelegramLink).one()
    assert row.owner_id == "existing"
    assert row.created_at == created_at
    db.close()
    engine.dispose()


def test_empty_legacy_chat_setting_is_a_no_op():
    engine, db = _database()

    assert telegram_link_service.backfill_legacy_link(db, "default-owner", "", "1234") == 0
    assert db.query(TelegramLink).count() == 0
    db.close()
    engine.dispose()
