"""Migration 0080 denormalises the owner onto chat_turn.

The backfill is the part worth testing. A turn's owner already exists on its
session, so leaving existing rows on the server default would answer every
"this user's last 50 turns" question wrong for all history - silently, and
only for the rows that predate the deploy.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "chat_turn"
COLUMN = "owner_id"
INDEX = "ix_chat_turn_owner_id"
PREVIOUS = "20260928_0079"
THIS = "20260929_0080"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def _seed_turn(connection, *, session_owner: str, session_id: int = 1) -> None:
    connection.execute(sa.text(
        "INSERT INTO chat_sessions (id, owner_id, title, created_at, updated_at) "
        f"VALUES ({session_id}, '{session_owner}', 't', "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    ))
    # Booleans and tool_calls default in Python, not in the database, so a raw
    # insert has to supply every NOT NULL column itself.
    connection.execute(sa.text(
        "INSERT INTO chat_turn (session_id, model, requested_model, attempts, "
        "failed_over, tool_calls, duration_ms, interrupted, cancelled, "
        "budget_exhausted, mcp_cached, prompt_sha256, created_at) "
        f"VALUES ({session_id}, 'm', 'auto', 1, 0, '[]', 10, 0, 0, 0, 0, 'abc', "
        "'2026-01-01 00:00:00')"
    ))


def test_the_column_and_index_exist_and_match_the_models(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")

    columns = {c["name"]: c for c in sa.inspect(engine).get_columns(TABLE)}
    assert COLUMN in columns
    assert not columns[COLUMN]["nullable"]
    assert (COLUMN,) in {
        tuple(index["column_names"]) for index in sa.inspect(engine).get_indexes(TABLE)
    }
    assert compare(url) == []
    engine.dispose()


def test_existing_turns_are_backfilled_from_their_session(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "backfill.db")
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        _seed_turn(connection, session_owner="usr_real_owner")

    command.upgrade(config, THIS)

    with engine.begin() as connection:
        owner = connection.execute(sa.text(f"SELECT {COLUMN} FROM {TABLE}")).scalar()
    assert owner == "usr_real_owner", "history must not be left on the fallback owner"
    engine.dispose()


def test_a_turn_whose_session_vanished_keeps_the_fallback(tmp_path, monkeypatch):
    """The default is for a row with no better answer, and that row exists."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "orphan.db")
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO chat_turn (session_id, model, requested_model, attempts, "
            "failed_over, tool_calls, duration_ms, interrupted, cancelled, "
            "budget_exhausted, mcp_cached, prompt_sha256, created_at) "
            "VALUES (999, 'm', 'auto', 1, 0, '[]', 10, 0, 0, 0, 0, 'abc', "
            "'2026-01-01 00:00:00')"
        ))
    command.upgrade(config, THIS)
    with engine.begin() as connection:
        owner = connection.execute(sa.text(f"SELECT {COLUMN} FROM {TABLE}")).scalar()
    assert owner == "default-owner"
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    # Named, not "-1": that walks back from head and silently starts exercising
    # the wrong migration once the next one lands.
    command.downgrade(config, PREVIOUS)
    assert COLUMN not in {c["name"] for c in sa.inspect(engine).get_columns(TABLE)}
    command.upgrade(config, "head")
    assert COLUMN in {c["name"] for c in sa.inspect(engine).get_columns(TABLE)}
    assert compare(url) == []
    engine.dispose()


def test_upgrading_twice_is_a_no_op(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "twice.db")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert COLUMN in {c["name"] for c in sa.inspect(engine).get_columns(TABLE)}
    engine.dispose()
