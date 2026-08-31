import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import NumberReviewQueue


class NumberReviewLinkedinUrlMigrationTests(unittest.TestCase):
    def test_upgrade_adds_column_and_downgrade_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)

            with Session(engine) as session:
                card = NumberReviewQueue(
                    owner_id="owner-1",
                    normalized_phone_number="+15551234567",
                    display_phone_number="(555) 123-4567",
                )
                session.add(card)
                session.commit()
                card_id = card.id

            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE number_review_queue DROP COLUMN linkedin_url")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260905_0043')")
            engine.dispose()

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "head")

                upgraded = sa.create_engine(database_url)
                columns = {column["name"] for column in sa.inspect(upgraded).get_columns("number_review_queue")}
                self.assertIn("linkedin_url", columns)
                with Session(upgraded) as session:
                    self.assertEqual(session.get(NumberReviewQueue, card_id).linkedin_url, "")
                upgraded.dispose()

                command.downgrade(config, "20260905_0043")
                downgraded = sa.create_engine(database_url)
                self.assertNotIn(
                    "linkedin_url",
                    {column["name"] for column in sa.inspect(downgraded).get_columns("number_review_queue")},
                )
                downgraded.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
