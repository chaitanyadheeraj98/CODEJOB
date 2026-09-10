"""Migration 0075 cleans derived reply bodies and leaves source bodies alone."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings

DIRTY = "Regards\n\nPranay\n\ncid:image001.jpg@01DCAA2C.53A49450\n\nHorizon Softech"
CLEAN = "No references here at all."


def _config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    return config


def _seed(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO email_conversations (id, owner_id, external_thread_id, status,"
            " last_message_at, unread_reply_count, created_at, updated_at)"
            " VALUES (1, 'owner', 'thread', 'replied', CURRENT_TIMESTAMP, 0,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ))
        for index, body in enumerate((DIRTY, CLEAN), start=1):
            conn.execute(
                sa.text(
                    "INSERT INTO email_reply_messages (owner_id, conversation_id, direction,"
                    " external_message_id, sender, body, snippet, received_at)"
                    " VALUES ('owner', 1, 'inbound', :m, 'sender', :b, '', CURRENT_TIMESTAMP)"
                ),
                {"m": f"m{index}", "b": body},
            )
        # The source column the migration must not touch, carrying the same
        # reference. recruiter_emails.body is persisted source, not display text.
        #
        # Built from reflection rather than a literal column list: this table
        # has many NOT NULL columns with no server default, and hardcoding them
        # makes the test fail every time an unrelated column is added.
        columns = {c["name"]: c for c in sa.inspect(conn).get_columns("recruiter_emails")}
        values: dict[str, object] = {}
        for name, column in columns.items():
            if name == "id" or column["nullable"] or column.get("default") is not None:
                continue
            values[name] = DIRTY if name == "body" else _placeholder(column["type"])
        conn.execute(
            sa.text(
                f"INSERT INTO recruiter_emails ({', '.join(values)})"
                f" VALUES ({', '.join(':' + name for name in values)})"
            ),
            values,
        )


def _placeholder(column_type: object) -> object:
    if isinstance(column_type, (sa.Integer, sa.Float)):
        return 0
    if isinstance(column_type, sa.Boolean):
        return False
    if isinstance(column_type, sa.DateTime):
        return "2026-09-10 12:00:00"
    return ""


def _bodies(engine: sa.Engine) -> list[str]:
    with engine.connect() as conn:
        return [r[0] for r in conn.execute(sa.text("SELECT body FROM email_reply_messages ORDER BY id"))]


def test_strips_inline_image_refs_from_reply_bodies(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / 'm75.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = _config(root)
    command.upgrade(config, "20260923_0074")
    engine = sa.create_engine(url)
    _seed(engine)

    command.upgrade(config, "head")

    dirty, untouched = _bodies(engine)
    assert "cid:" not in dirty
    assert "Pranay" in dirty and "Horizon Softech" in dirty
    assert "\n\n\n" not in dirty, "the removed line must not leave a gap"
    assert untouched == CLEAN, "rows without a reference stay byte-identical"

    # Persisted source is deliberately left alone; see the migration docstring.
    with engine.connect() as conn:
        assert "cid:" in conn.execute(sa.text("SELECT body FROM recruiter_emails")).scalar()

    # Replay: downgrade is a no-op, so re-running must be idempotent rather
    # than stripping a second time or crashing.
    command.downgrade(config, "-1")
    command.upgrade(config, "head")
    assert _bodies(engine) == [dirty, untouched]
    engine.dispose()


def test_runs_on_a_database_with_no_messages(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)

    command.upgrade(_config(root), "head")

    engine = sa.create_engine(url)
    assert _bodies(engine) == []
    engine.dispose()
