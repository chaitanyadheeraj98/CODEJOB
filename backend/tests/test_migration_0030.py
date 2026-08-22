import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class EmailReplyNotifiedAtMigrationTests(unittest.TestCase):
    def test_upgrade_backfills_existing_rows_and_downgrade_drops_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260823_0029")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    connection.execute(
                        sa.text(
                            "INSERT INTO email_reply_messages "
                            "(owner_id, conversation_id, direction, external_message_id, sender, body, snippet, received_at) "
                            "VALUES ('default-owner', 1, 'inbound', 'ext-1', 'a@example.com', 'body', 'body', '2026-08-20 00:00:00')"
                        )
                    )
                engine.dispose()

                command.upgrade(config, "20260824_0030")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                columns = {column["name"] for column in inspector.get_columns("email_reply_messages")}
                self.assertIn("notified_at", columns)
                with engine.begin() as connection:
                    row = connection.execute(
                        sa.text("SELECT notified_at, received_at FROM email_reply_messages WHERE external_message_id = 'ext-1'")
                    ).one()
                self.assertIsNotNone(row.notified_at)
                self.assertEqual(row.notified_at, row.received_at)
                engine.dispose()

                command.downgrade(config, "20260823_0029")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                columns = {column["name"] for column in inspector.get_columns("email_reply_messages")}
                self.assertNotIn("notified_at", columns)
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
