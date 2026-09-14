from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "users"
COLUMN = "deletion_requested_at"
INDEX = "ix_users_deletion_requested_at"
PREVIOUS = "20260930_0081"
THIS = "20261001_0082"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def test_column_and_index_match_the_model(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")

    columns = {column["name"]: column for column in sa.inspect(engine).get_columns(TABLE)}
    assert columns[COLUMN]["nullable"]
    assert (COLUMN,) in {
        tuple(index["column_names"]) for index in sa.inspect(engine).get_indexes(TABLE)
    }
    assert compare(url) == []
    engine.dispose()


def test_existing_users_are_not_scheduled_for_deletion(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "existing.db")
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO users (owner_id, email, display_name, is_admin, created_at) "
            "VALUES ('usr_existing', 'existing@example.com', '', 0, '2026-01-01 00:00:00')"
        ))

    command.upgrade(config, THIS)

    with engine.begin() as connection:
        assert connection.execute(sa.text(f"SELECT {COLUMN} FROM {TABLE}")).scalar() is None
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert COLUMN not in {column["name"] for column in sa.inspect(engine).get_columns(TABLE)}
    command.upgrade(config, "head")
    assert COLUMN in {column["name"] for column in sa.inspect(engine).get_columns(TABLE)}
    assert compare(url) == []
    engine.dispose()


def test_upgrading_twice_is_a_no_op(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "twice.db")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    indexes = [index for index in sa.inspect(engine).get_indexes(TABLE) if index["name"] == INDEX]
    assert len(indexes) == 1
    engine.dispose()
