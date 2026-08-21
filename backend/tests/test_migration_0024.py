import os
import tempfile
import unittest
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class ResumeEnrichmentMigrationTests(unittest.TestCase):
    def test_upgrade_adds_nullable_resume_content_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{(Path(directory) / 'migration.db').as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260820_0023")
                engine = sa.create_engine(database_url)
                before = {column["name"] for column in sa.inspect(engine).get_columns("resume_assets")}
                engine.dispose()
                self.assertNotIn("content_markdown", before)

                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                columns = {column["name"]: column for column in sa.inspect(engine).get_columns("resume_assets")}
                engine.dispose()
                for name in ("content_markdown", "content_summary", "content_evidence_json"):
                    self.assertIn(name, columns)
                    self.assertTrue(columns[name]["nullable"])
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
