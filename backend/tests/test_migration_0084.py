from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

PREVIOUS = "20261002_0083"
THIS = "20261003_0084"
TABLE = "telegram_links"


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

    path = Path(__file__).parents[1] / "alembic" / "versions" / f"{THIS}_telegram_links.py"
    spec = importlib.util.spec_from_file_location("revision_0084", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_constraints_and_bigint_match_the_model(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")
    inspector = sa.inspect(engine)

    constraints = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(TABLE)}
    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    assert ("owner_id",) in constraints
    assert ("chat_id",) in constraints
    assert isinstance(columns["chat_id"]["type"], sa.BigInteger)
    assert compare(url) == []
    engine.dispose()


def test_column_factory_returns_fresh_objects():
    module = _revision_module()
    first, second = module._columns(), module._columns()
    assert all(a is not b for a, b in zip(first, second))


def test_upgrade_downgrade_upgrade_replays(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert not sa.inspect(engine).has_table(TABLE)

    command.upgrade(config, "head")
    assert sa.inspect(engine).has_table(TABLE)
    assert compare(url) == []
    engine.dispose()
