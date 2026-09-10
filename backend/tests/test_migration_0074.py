from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from scripts.verify_schema_equivalence import compare


def test_label_migration_round_trip(tmp_path, monkeypatch):
    root = Path(__file__).parents[1]
    url = f"sqlite:///{(tmp_path / 'labels.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(settings, "database_url", url)
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "20260909_0073")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO email_conversations (owner_id, root_recruiter_email_id, external_thread_id, status, last_message_at, unread_reply_count, created_at, updated_at) VALUES ('owner', 1, 'thread', 'sent', CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    command.upgrade(config, "head")
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT origin FROM email_conversations")).scalar() == "sent"
        assert sa.inspect(conn).get_columns("email_conversations")[2]["nullable"]
        for table in ("gmail_labels", "tracked_threads", "recruiter_watches"):
            columns = {c["name"]: c for c in sa.inspect(conn).get_columns(table)}
            for index in sa.inspect(conn).get_indexes(table):
                assert all(not isinstance(columns[c]["type"], sa.Text) for c in index["column_names"])
    assert compare(url) == []
    command.downgrade(config, "-1")
    with engine.connect() as conn:
        assert not sa.inspect(conn).has_table("gmail_labels")
        assert conn.execute(sa.text("SELECT external_thread_id FROM email_conversations")).scalar() == "thread"
    command.upgrade(config, "head")
    engine.dispose()
