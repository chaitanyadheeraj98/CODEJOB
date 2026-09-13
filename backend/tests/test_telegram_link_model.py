from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import Base
from app.models import TelegramLink


def _row(owner_id: str, chat_id: int | None = None) -> TelegramLink:
    return TelegramLink(owner_id=owner_id, chat_id=chat_id, created_at=datetime.now(UTC))


def test_owner_and_chat_are_unique_but_pending_rows_can_coexist():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([_row("owner-a"), _row("owner-b")])
        db.commit()

        db.add(_row("owner-a"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add_all([_row("owner-c", 42), _row("owner-d", 42)])
        with pytest.raises(IntegrityError):
            db.commit()
    engine.dispose()


def test_chat_id_accepts_values_above_32_bits():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    chat_id = 2**31 + 7
    with Session(engine) as db:
        db.add(_row("owner-a", chat_id))
        db.commit()
        assert db.query(TelegramLink).one().chat_id == chat_id
    engine.dispose()
