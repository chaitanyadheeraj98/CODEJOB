from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "provider_credentials"
PREVIOUS = "20260926_0077"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def test_provider_credentials_shape_and_indexes(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")

    inspector = sa.inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns(TABLE)}
    assert set(columns) == {
        "id", "owner_id", "provider", "api_key_encrypted", "base_url", "label",
        "last_used_at", "last_error", "created_at",
    }
    assert columns["last_used_at"]["nullable"]
    assert {tuple(index["column_names"]) for index in inspector.get_indexes(TABLE)} == {
        ("owner_id",), ("provider",),
    }
    assert compare(url) == []
    engine.dispose()


def test_one_key_per_owner_and_provider(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "unique.db")
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO provider_credentials (owner_id, provider) VALUES ('owner', 'ollama')"
        ))
        try:
            connection.execute(sa.text(
                "INSERT INTO provider_credentials (owner_id, provider) VALUES ('owner', 'ollama')"
            ))
        except sa.exc.IntegrityError:
            pass
        else:
            raise AssertionError("duplicate owner/provider must be refused")
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    command.stamp(config, PREVIOUS)
    command.upgrade(config, "head")
    assert sa.inspect(engine).has_table(TABLE)
    engine.dispose()


def test_round_trips_down_and_up(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "roundtrip.db")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert not sa.inspect(engine).has_table(TABLE)
    command.upgrade(config, "head")
    assert sa.inspect(engine).has_table(TABLE)
    engine.dispose()
