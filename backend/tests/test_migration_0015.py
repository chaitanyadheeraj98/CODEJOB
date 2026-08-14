import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from app.db import Base


class ChatMigrationTests(unittest.TestCase):
    def test_upgrade_and_downgrade_chat_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE chat_messages")
                connection.exec_driver_sql("DROP TABLE chat_sessions")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260810_0014')")
            engine.dispose()

            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                self.assertIn("chat_sessions", sa.inspect(engine).get_table_names())
                self.assertIn("chat_messages", sa.inspect(engine).get_table_names())
                engine.dispose()

                command.downgrade(config, "20260810_0014")
                engine = sa.create_engine(database_url)
                self.assertNotIn("chat_sessions", sa.inspect(engine).get_table_names())
                self.assertNotIn("chat_messages", sa.inspect(engine).get_table_names())
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
