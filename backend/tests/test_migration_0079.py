"""Migration 0079 adds the tombstone that E2's overlay needs.

The column arrived in `models.py` before it arrived in a migration, so the
model and the database disagreed: `alembic upgrade head` left the live table
without `suppressed` while every query selected it. Caught by checking the
running database rather than by the suite, which builds its schema from the
models and so can never see this class of drift.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "canonical_entity_taxonomy_entries"
COLUMN = "suppressed"
PREVIOUS = "20260927_0078"
THIS = "20260928_0079"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def test_the_column_exists_and_matches_the_models(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")

    columns = {c["name"]: c for c in sa.inspect(engine).get_columns(TABLE)}
    assert COLUMN in columns
    assert not columns[COLUMN]["nullable"]
    assert compare(url) == []
    engine.dispose()


def test_existing_rows_keep_todays_behaviour(tmp_path, monkeypatch):
    """The server default is the backfill: nothing is tombstoned by upgrading."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "backfill.db")
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        connection.execute(sa.text(
            # created_at/updated_at default in Python, not in the database, so a
            # raw insert has to supply them.
            f"INSERT INTO {TABLE} "
            "(owner_id, entity_type, canonical_name, status, created_at, updated_at) "
            "VALUES ('owner', 'role', 'Java Developer', 'approved', "
            "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ))
    command.upgrade(config, THIS)
    with engine.begin() as connection:
        value = connection.execute(sa.text(f"SELECT {COLUMN} FROM {TABLE}")).scalar()
    assert value in (0, False), "an existing approved entry must not become a tombstone"
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    # Named explicitly rather than "-1": `downgrade("-1")` walks back from the
    # *head*, which is this revision only until the next one lands - at which
    # point the test quietly starts exercising the wrong migration.
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


def test_the_column_is_not_indexed(tmp_path, monkeypatch):
    """A boolean with one dominant value indexes badly, and this is only ever
    read inside an owner+entity_type scan that is already indexed."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "index.db")
    command.upgrade(config, "head")
    indexed = {
        tuple(index["column_names"]) for index in sa.inspect(engine).get_indexes(TABLE)
    }
    assert (COLUMN,) not in indexed
    engine.dispose()
