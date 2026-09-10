"""Migration 0076 creates gmail_credentials, and is replay-safe from the start.

0074 was not, and cost a debugging cycle when an unrelated migration test hit
"table gmail_labels already exists". The replay assertions here exist so the
same retrofit is never needed twice.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

TABLE = "gmail_credentials"


def _config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    # env.py discards sqlalchemy.url and reads DATABASE_URL; setting the option
    # alone would migrate the real dev database.
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    return _config(root), sa.create_engine(url), url


def test_creates_the_table_with_the_expected_shape(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "c76.db")

    command.upgrade(config, "head")

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        assert inspector.has_table(TABLE)
        columns = {c["name"]: c for c in inspector.get_columns(TABLE)}

        assert columns["refresh_token_encrypted"]["nullable"], "absent on re-consent"
        assert columns["google_subject"]["nullable"], "needs the openid scope"
        assert columns["expires_at"]["nullable"]
        assert columns["revoked_at"]["nullable"]
        assert not columns["owner_id"]["nullable"]

        # Index discipline from the 0074 docstring: only filtered columns, and
        # never a Text column.
        indexed = {tuple(i["column_names"]) for i in inspector.get_indexes(TABLE)}
        assert ("owner_id",) in indexed
        assert ("google_email",) in indexed, "Pub/Sub routes by mailbox address"
        assert ("revoked_at",) in indexed, "checked on every credential read"
        for index in inspector.get_indexes(TABLE):
            for column in index["column_names"]:
                assert not isinstance(columns[column]["type"], sa.Text)

    # The model and the migration must not have drifted.
    assert compare(url) == []
    engine.dispose()


def test_one_row_per_owner(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "u76.db")
    command.upgrade(config, "head")

    with engine.begin() as conn:
        conn.execute(sa.text(f"INSERT INTO {TABLE} (owner_id) VALUES ('owner')"))

    with engine.begin() as conn:
        try:
            conn.execute(sa.text(f"INSERT INTO {TABLE} (owner_id) VALUES ('owner')"))
        except sa.exc.IntegrityError:
            pass
        else:  # pragma: no cover - only reached if the constraint is missing
            raise AssertionError("a second credential for the same owner must be refused")
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    """Running upgrade twice must not raise, the fault 0074 shipped with."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "r76.db")

    command.upgrade(config, "head")
    command.stamp(config, "20260924_0075")
    command.upgrade(config, "head")

    with engine.connect() as conn:
        assert sa.inspect(conn).has_table(TABLE)
    engine.dispose()


def test_round_trips_down_and_up(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "d76.db")
    command.upgrade(config, "head")

    command.downgrade(config, "-1")
    with engine.connect() as conn:
        assert not sa.inspect(conn).has_table(TABLE)

    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert sa.inspect(conn).has_table(TABLE)
    engine.dispose()
