"""Migration 0083: Gmail push watch state on `gmail_credentials`.

Six added columns, no table and no index. The two things worth proving are
that an existing connection survives the upgrade untouched - people are
connected right now, and a migration that made them reconnect would be a
self-inflicted outage - and that the whole thing replays, because
`alembic upgrade head` runs on every backend boot.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "gmail_credentials"
COLUMNS = (
    "gmail_history_id",
    "gmail_watch_expiration_at",
    "gmail_watch_renewed_at",
    "gmail_last_notification_at",
    "gmail_last_event_processed_at",
    "gmail_watch_last_error",
)
PREVIOUS = "20261001_0082"
THIS = "20261002_0083"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def _columns(engine) -> dict[str, dict]:
    return {column["name"]: column for column in sa.inspect(engine).get_columns(TABLE)}


def _revision_module():
    """Load the revision by path; `alembic/versions` is not an importable
    package and the file name starts with a digit."""
    import importlib.util

    path = Path(__file__).parents[1] / "alembic" / "versions" / f"{THIS}_gmail_pubsub_watch_state.py"
    spec = importlib.util.spec_from_file_location("revision_0083", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_columns_match_the_model(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "shape.db")
    command.upgrade(config, "head")

    columns = _columns(engine)
    for name in COLUMNS:
        assert name in columns, f"{name} is missing"
    # Five nullable, because "no watch has ever been registered" is a real
    # state and null is how it reads. The error is not: an absent error is "".
    for name in COLUMNS[:-1]:
        assert columns[name]["nullable"], f"{name} should tolerate a mailbox with no watch"
    assert not columns["gmail_watch_last_error"]["nullable"]
    assert compare(url) == []
    engine.dispose()


def test_no_index_was_added(tmp_path, monkeypatch):
    """`owner_id` is unique and `google_email` is already indexed for exactly
    the Pub/Sub lookup. Nothing here is filtered on, and an index on a column
    only ever read through one of those is cost with no query to pay for it."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "indexes.db")
    command.upgrade(config, PREVIOUS)
    before = {index["name"] for index in sa.inspect(engine).get_indexes(TABLE)}

    command.upgrade(config, THIS)

    assert {index["name"] for index in sa.inspect(engine).get_indexes(TABLE)} == before
    engine.dispose()


def test_an_existing_connection_survives_untouched(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "existing.db")
    command.upgrade(config, PREVIOUS)
    with engine.begin() as connection:
        connection.execute(sa.text(
            "INSERT INTO gmail_credentials "
            "(owner_id, google_email, access_token_encrypted, refresh_token_encrypted, "
            " token_uri, scopes_json, connected_at, last_error) "
            "VALUES ('usr_existing', 'live@example.com', 'cipher-a', 'cipher-b', "
            "'https://oauth2.googleapis.com/token', '[]', '2026-01-01 00:00:00', '')"
        ))

    command.upgrade(config, THIS)

    with engine.begin() as connection:
        row = connection.execute(sa.text(
            "SELECT google_email, refresh_token_encrypted, gmail_history_id, "
            "gmail_watch_expiration_at, gmail_watch_last_error FROM gmail_credentials"
        )).one()
    # The connection is intact: nobody reconnects because of this migration.
    assert row.google_email == "live@example.com"
    assert row.refresh_token_encrypted == "cipher-b"
    # And it reads as what it is - a mailbox that has never had a watch.
    assert row.gmail_history_id is None
    assert row.gmail_watch_expiration_at is None
    assert row.gmail_watch_last_error == ""
    engine.dispose()


def test_the_column_definitions_are_not_reusable_objects(tmp_path, monkeypatch):
    """`op.add_column` attaches the `Column` it is given to a table, and a
    second attachment raises. The revision hands out fresh objects.

    Pinned directly because no end-to-end run catches it: alembic re-executes
    the revision file on every `command.upgrade`, so a module-level constant is
    rebuilt each time and `test_is_replay_safe` below stays green either way.
    """
    module = _revision_module()

    first, second = module._columns(), module._columns()

    assert [column.name for column in first] == list(COLUMNS)
    assert all(a is not b for a, b in zip(first, second))


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "replay.db")
    command.upgrade(config, "head")
    command.downgrade(config, PREVIOUS)
    assert not set(COLUMNS) & set(_columns(engine))

    command.upgrade(config, "head")

    assert set(COLUMNS) <= set(_columns(engine))
    assert compare(url) == []
    engine.dispose()


def test_the_downgrade_takes_only_its_own_columns(tmp_path, monkeypatch):
    """It runs against a table holding live OAuth credentials. Reverting this
    revision must cost the watch state and nothing else."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "downgrade.db")
    command.upgrade(config, PREVIOUS)
    before = set(_columns(engine))
    command.upgrade(config, THIS)

    command.downgrade(config, PREVIOUS)

    assert set(_columns(engine)) == before
    engine.dispose()


def test_upgrading_twice_is_a_no_op(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "twice.db")
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert compare(url) == []
    engine.dispose()
