"""Migration 0077 creates users and user_sessions.

The assertion worth reading twice is `test_there_is_no_password_column`. There
is no signup and no local credential by design; a password column appearing
later would mean the access model had silently changed.
"""

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare

USERS = "users"
SESSIONS = "user_sessions"


def _prepare(tmp_path, monkeypatch, name: str) -> tuple[Config, sa.Engine, str]:
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / name).as_posix()}"
    # env.py reads DATABASE_URL and discards sqlalchemy.url; set both or this
    # migrates the real dev database.
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config, sa.create_engine(url), url


def test_creates_both_tables_with_the_expected_shape(tmp_path, monkeypatch):
    config, engine, url = _prepare(tmp_path, monkeypatch, "u77.db")

    command.upgrade(config, "head")

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        assert inspector.has_table(USERS)
        assert inspector.has_table(SESSIONS)

        users = {c["name"]: c for c in inspector.get_columns(USERS)}
        assert not users["owner_id"]["nullable"]
        assert not users["email"]["nullable"]
        assert users["google_subject"]["nullable"], "a row can predate the identity scopes"
        assert users["disabled_at"]["nullable"]
        assert users["last_login_at"]["nullable"]

        sessions = {c["name"]: c for c in inspector.get_columns(SESSIONS)}
        assert not sessions["token_hash"]["nullable"]
        assert not sessions["expires_at"]["nullable"]
        assert sessions["revoked_at"]["nullable"]

    assert compare(url) == [], "model and migration must not drift"
    engine.dispose()


def test_there_is_no_password_column(tmp_path, monkeypatch):
    """Access is Google's test-user list. A local credential is not part of it."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "p77.db")
    command.upgrade(config, "head")

    with engine.connect() as conn:
        names = {c["name"] for c in sa.inspect(conn).get_columns(USERS)}

    assert not any("password" in name or "secret" in name for name in names), sorted(names)
    engine.dispose()


def test_indexes_cover_the_lookup_paths_and_no_text_column(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "i77.db")
    command.upgrade(config, "head")

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        for table, expected in (
            (USERS, {("owner_id",), ("email",), ("is_admin",), ("disabled_at",)}),
            (SESSIONS, {("user_id",), ("token_hash",), ("expires_at",), ("revoked_at",)}),
        ):
            columns = {c["name"]: c for c in inspector.get_columns(table)}
            indexed = {tuple(i["column_names"]) for i in inspector.get_indexes(table)}
            assert expected <= indexed, f"{table}: {indexed}"
            for index in inspector.get_indexes(table):
                for column in index["column_names"]:
                    assert not isinstance(columns[column]["type"], sa.Text)
    engine.dispose()


@pytest.mark.parametrize(
    "column,first,second",
    [
        ("owner_id", "usr_a", "usr_a"),
        ("email", "same@example.com", "same@example.com"),
        ("google_subject", "sub-1", "sub-1"),
    ],
)
def test_identity_columns_are_unique(tmp_path, monkeypatch, column, first, second):
    config, engine, _ = _prepare(tmp_path, monkeypatch, f"x77-{column}.db")
    command.upgrade(config, "head")
    base = {"owner_id": "usr_a", "email": "a@example.com", "google_subject": "sub-a"}

    with engine.begin() as conn:
        row = {**base, column: first}
        conn.execute(sa.text(
            "INSERT INTO users (owner_id, email, google_subject) VALUES (:owner_id, :email, :google_subject)"
        ), row)

    with engine.begin() as conn:
        clash = {"owner_id": "usr_b", "email": "b@example.com", "google_subject": "sub-b", column: second}
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text(
                "INSERT INTO users (owner_id, email, google_subject) VALUES (:owner_id, :email, :google_subject)"
            ), clash)
    engine.dispose()


def test_two_users_may_both_have_no_google_subject(tmp_path, monkeypatch):
    """A unique constraint must not stop a second pre-identity row existing."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "n77.db")
    command.upgrade(config, "head")

    with engine.begin() as conn:
        for index in (1, 2):
            conn.execute(sa.text(
                "INSERT INTO users (owner_id, email) VALUES (:o, :e)"
            ), {"o": f"usr_{index}", "e": f"user{index}@example.com"})

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar() == 2
    engine.dispose()


def test_deleting_a_user_takes_their_sessions_with_them(tmp_path, monkeypatch):
    """CASCADE, so G3's purge does not trip over sessions the way a RESTRICT would."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "c77.db")
    command.upgrade(config, "head")

    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys=ON"))
        conn.execute(sa.text("INSERT INTO users (id, owner_id, email) VALUES (1, 'usr_a', 'a@example.com')"))
        conn.execute(sa.text(
            "INSERT INTO user_sessions (user_id, token_hash, expires_at)"
            " VALUES (1, 'hash-1', '2026-12-01 00:00:00')"
        ))
        conn.execute(sa.text("DELETE FROM users WHERE id = 1"))

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM user_sessions")).scalar() == 0
    engine.dispose()


def test_the_migration_creates_no_rows(tmp_path, monkeypatch):
    """default-owner keeps working through the tenancy fallback, not a seeded row."""
    config, engine, _ = _prepare(tmp_path, monkeypatch, "e77.db")

    command.upgrade(config, "head")

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM users")).scalar() == 0
    engine.dispose()


def test_is_replay_safe(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "r77.db")

    command.upgrade(config, "head")
    command.stamp(config, "20260925_0076")
    command.upgrade(config, "head")

    with engine.connect() as conn:
        assert sa.inspect(conn).has_table(USERS)
        assert sa.inspect(conn).has_table(SESSIONS)
    engine.dispose()


def test_round_trips_down_and_up(tmp_path, monkeypatch):
    config, engine, _ = _prepare(tmp_path, monkeypatch, "d77.db")
    command.upgrade(config, "head")

    command.downgrade(config, "20260925_0076")
    with engine.connect() as conn:
        assert not sa.inspect(conn).has_table(USERS)
        assert not sa.inspect(conn).has_table(SESSIONS)

    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert sa.inspect(conn).has_table(USERS)
    engine.dispose()
