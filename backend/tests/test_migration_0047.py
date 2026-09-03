import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import PremiumContactPhone, PremiumNumberContact


class ContactPhoneLabelMigrationTests(unittest.TestCase):
    def test_upgrade_adds_column_and_downgrade_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)

            with Session(engine) as session:
                contact = PremiumNumberContact(
                    owner_id="owner-1",
                    normalized_phone_number="+15551234567",
                    display_phone_number="(555) 123-4567",
                )
                session.add(contact)
                session.flush()
                phone = PremiumContactPhone(
                    owner_id="owner-1",
                    premium_contact_id=contact.id,
                    normalized_phone_number="+15559998888",
                )
                session.add(phone)
                session.commit()
                phone_id = phone.id

            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE premium_contact_phones DROP COLUMN label")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260908_0046')")
            engine.dispose()

            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
                command.upgrade(config, "head")

                upgraded = sa.create_engine(database_url)
                columns = {column["name"] for column in sa.inspect(upgraded).get_columns("premium_contact_phones")}
                self.assertIn("label", columns)
                with Session(upgraded) as session:
                    self.assertEqual(session.get(PremiumContactPhone, phone_id).label, "")
                upgraded.dispose()

                command.downgrade(config, "20260908_0046")
                downgraded = sa.create_engine(database_url)
                self.assertNotIn(
                    "label",
                    {column["name"] for column in sa.inspect(downgraded).get_columns("premium_contact_phones")},
                )
                downgraded.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
