import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.config import settings
from scripts.verify_schema_equivalence import compare


def test_chat_turn_migration_fresh_repeat_and_schema(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    engine = sa.create_engine(url)
    try:
        spec = importlib.util.spec_from_file_location("chat_turn_migration", root / "alembic/versions/20260909_0073_chat_turn.py")
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        with engine.begin() as connection:
            migration.op = Operations(MigrationContext.configure(connection))
            migration.upgrade()
            assert {tuple(i["column_names"]) for i in sa.inspect(connection).get_indexes("chat_turn")} == {("session_id",), ("created_at",)}
        assert compare(url) == []
    finally:
        engine.dispose()
