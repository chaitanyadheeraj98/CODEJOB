import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import UserSettings


class MultiEmployerCcMigrationTests(unittest.TestCase):
    def test_upgrade_adds_lists_and_backfills_legacy_preferred_cc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                engine = sa.create_engine(database_url)
                Base.metadata.create_all(engine)
                with Session(engine) as session:
                    session.add(UserSettings(owner_id="owner", preferred_employer_cc_email="Ops@Example.com"))
                    session.commit()
                with engine.begin() as connection:
                    connection.exec_driver_sql("ALTER TABLE user_settings DROP COLUMN preferred_employer_cc_emails")
                    connection.exec_driver_sql("ALTER TABLE user_settings DROP COLUMN default_employer_cc_emails")
                    connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                    connection.exec_driver_sql(
                        "INSERT INTO alembic_version(version_num) VALUES ('20260805_0013')"
                    )
                engine.dispose()
                command.upgrade(config, "head")
            finally:
                settings.database_url = previous_url

            engine = sa.create_engine(database_url)
            try:
                columns = {column["name"] for column in sa.inspect(engine).get_columns("user_settings")}
                with engine.connect() as connection:
                    revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
                    values = connection.exec_driver_sql(
                        "SELECT preferred_employer_cc_emails, default_employer_cc_emails FROM user_settings"
                    ).one()
                self.assertEqual(revision, ScriptDirectory.from_config(config).get_current_head())
                self.assertIn("preferred_employer_cc_emails", columns)
                self.assertIn("default_employer_cc_emails", columns)
                self.assertEqual(values, ("ops@example.com", ""))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
