import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from app.db import Base
# Imported for the side effect: without it Base.metadata is empty when this
# file runs alone, create_all() makes no tables, and the DROP below fails.
import app.models  # noqa: F401

TEXT_COLUMNS = {"file_path", "content_markdown", "extraction_error"}


class ChatAttachmentMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_chat_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE chat_attachments")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260917_0055')")
            engine.dispose()

            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertIn("chat_attachments", inspector.get_table_names())

                indexed = {
                    column
                    for index in inspector.get_indexes("chat_attachments")
                    for column in index["column_names"]
                }
                self.assertEqual(
                    indexed, {"id", "owner_id", "session_id", "message_id", "sha256"}
                )
                # This migration runs on backend boot. An index over an unbounded
                # column blew the Postgres btree key limit once already.
                self.assertFalse(indexed & TEXT_COLUMNS)
                engine.dispose()

                # Idempotent: boot runs `upgrade head` every time.
                command.upgrade(config, "head")

                command.downgrade(config, "20260917_0055")
                engine = sa.create_engine(database_url)
                self.assertNotIn("chat_attachments", sa.inspect(engine).get_table_names())
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
