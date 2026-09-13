from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

PREVIOUS = "20261003_0084"
THIS = "20261004_0085"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def _revision_module():
    import importlib.util

    path = Path(__file__).parents[1] / "alembic" / "versions" / f"{THIS}_telegram_chat_sessions.py"
    spec = importlib.util.spec_from_file_location("revision_0085", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_telegram_chat_columns_index_and_foreign_key_match_model(tmp_path, monkeypatch) -> None:
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    session_columns = {item["name"]: item for item in inspector.get_columns("chat_sessions")}
    link_columns = {item["name"]: item for item in inspector.get_columns("telegram_links")}
    indexes = {item["name"]: item for item in inspector.get_indexes("chat_sessions")}
    foreign_keys = inspector.get_foreign_keys("telegram_links")
    assert isinstance(session_columns["origin"]["type"], sa.String)
    assert session_columns["origin"]["type"].length == 20
    assert isinstance(link_columns["chat_session_id"]["type"], sa.Integer)
    assert indexes["ix_chat_sessions_origin_updated_at"]["column_names"] == ["origin", "updated_at"]
    assert any(item["referred_table"] == "chat_sessions" for item in foreign_keys)
    assert compare(url) == []
    engine.dispose()


def test_column_factory_returns_fresh_objects() -> None:
    module = _revision_module()
    first, second = module._columns(), module._columns()
    assert all(a is not b for a, b in zip(first, second))


def test_upgrade_downgrade_upgrade_replays(tmp_path, monkeypatch) -> None:
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert "origin" not in {item["name"] for item in sa.inspect(engine).get_columns("chat_sessions")}
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert compare(url) == []
    engine.dispose()
