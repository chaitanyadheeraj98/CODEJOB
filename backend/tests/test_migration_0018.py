import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.config import settings
from scripts.verify_schema_equivalence import compare


BASELINE_TABLES = {
    "attachment_assets",
    "candidate_resume_matches",
    "draft_edit_feedback",
    "employer_numbers",
    "number_review_queue",
    "premium_number_leads",
    "premium_number_contacts",
    "productivity_events",
    "recipient_routing_feedback",
    "recruiter_emails",
    "recruiter_numbers",
    "recruiter_opportunities",
    "resume_assets",
    "sync_runs",
    "user_settings",
}


class DeclarativeBaselineMigrationTests(unittest.TestCase):
    def test_full_fresh_sqlite_chain_matches_models_and_downgrades_safely(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "fresh.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                try:
                    inspector = sa.inspect(engine)
                    self.assertTrue(BASELINE_TABLES <= set(inspector.get_table_names()))
                    with engine.connect() as connection:
                        revision = connection.exec_driver_sql(
                            "SELECT version_num FROM alembic_version"
                        ).scalar_one()
                    self.assertEqual(revision, ScriptDirectory.from_config(config).get_current_head())
                    self.assertEqual(compare(database_url), [])
                finally:
                    engine.dispose()

                command.downgrade(config, "20260815_0016")
                engine = sa.create_engine(database_url)
                try:
                    self.assertTrue(
                        BASELINE_TABLES.isdisjoint(sa.inspect(engine).get_table_names())
                    )
                finally:
                    engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
