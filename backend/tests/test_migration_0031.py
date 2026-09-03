import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ["DEBUG"] = "false"

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings


class OpportunityLineageMigrationTests(unittest.TestCase):
    @staticmethod
    def _insert(connection, table_name: str, **values: object) -> None:
        table = sa.Table(table_name, sa.MetaData(), autoload_with=connection)
        payload: dict[str, object] = {}
        for column in table.columns:
            if column.name in values:
                payload[column.name] = values[column.name]
            elif column.primary_key and column.autoincrement:
                continue
            elif not column.nullable and column.server_default is None:
                if isinstance(column.type, sa.Boolean):
                    payload[column.name] = False
                elif isinstance(column.type, (sa.Integer, sa.Float)):
                    payload[column.name] = 0
                elif isinstance(column.type, sa.DateTime):
                    payload[column.name] = datetime(2026, 8, 1)
                else:
                    payload[column.name] = ""
        connection.execute(table.insert().values(**payload))

    def test_populated_upgrade_backfills_and_downgrade_reverses_schema(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                backend_path = Path(__file__).resolve().parents[1]
                config = Config(str(backend_path / "alembic.ini"))
                config.set_main_option("script_location", str(backend_path / "alembic"))
                config.set_main_option("sqlalchemy.url", database_url)
                command.upgrade(config, "20260824_0030")

                engine = sa.create_engine(database_url)
                with engine.begin() as connection:
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=101,
                        owner_id="default-owner",
                        external_message_id="gmail-source-101",
                    )
                    self._insert(
                        connection,
                        "external_feed_sources",
                        id=201,
                        owner_id="default-owner",
                        source_type="nvoids",
                    )
                    self._insert(
                        connection,
                        "external_opportunities",
                        id=301,
                        owner_id="default-owner",
                        feed_source_id=201,
                        source_type="nvoids",
                        external_post_id="nvoids-301",
                        dedupe_hash="nvoids-hash-301",
                    )
                    for contact_id in range(401, 406):
                        self._insert(
                            connection,
                            "premium_number_contacts",
                            id=contact_id,
                            owner_id="default-owner",
                            normalized_phone_number=f"1555000{contact_id}",
                            display_phone_number=f"+1 555 000 {contact_id}",
                        )
                    opportunity_rows = [
                        {
                            "id": 501,
                            "recruiter_number_id": 401,
                            "source_type": "gmail",
                            "source_email_id": 101,
                            "gmail_message_id": "opp-gmail",
                            "status": "New",
                        },
                        {
                            "id": 502,
                            "recruiter_number_id": 402,
                            "source_type": "nvoids",
                            "external_opportunity_id": 301,
                            "gmail_message_id": "opp-nvoids",
                            "status": "New",
                        },
                        {
                            "id": 503,
                            "recruiter_number_id": 403,
                            "source_type": "gmail",
                            "gmail_message_id": "opp-no-source",
                            "status": "Closed",
                        },
                        {
                            "id": 504,
                            "recruiter_number_id": 404,
                            "source_type": "gmail",
                            "source_email_id": 999,
                            "gmail_message_id": "opp-dangling",
                            "status": "New",
                        },
                        {
                            "id": 505,
                            "recruiter_number_id": 405,
                            "source_type": "gmail",
                            "source_email_id": 101,
                            "gmail_message_id": "opp-classified",
                            "status": "Contacted",
                        },
                    ]
                    for row in opportunity_rows:
                        self._insert(
                            connection,
                            "recruiter_opportunities",
                            owner_id="default-owner",
                            created_at=datetime(2026, 7, row["id"] - 500),
                            **row,
                        )
                    self._insert(
                        connection,
                        "number_review_queue",
                        id=601,
                        owner_id="default-owner",
                        source_external_opportunity_id=301,
                        normalized_phone_number="1555000601",
                        display_phone_number="+1 555 000 0601",
                        state="pending",
                        created_at=datetime(2026, 7, 10),
                    )
                    self._insert(
                        connection,
                        "number_review_queue",
                        id=602,
                        owner_id="default-owner",
                        source_email_id=101,
                        normalized_phone_number="1555000602",
                        display_phone_number="+1 555 000 0602",
                        state="classified_recruiter",
                        created_at=datetime(2026, 7, 11),
                    )
                engine.dispose()

                command.upgrade(config, "20260825_0031")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertIn("lineage_id", {column["name"] for column in inspector.get_columns("number_review_queue")})
                self.assertIn(
                    "fk_number_review_queue_lineage",
                    {foreign_key["name"] for foreign_key in inspector.get_foreign_keys("number_review_queue")},
                )
                with engine.begin() as connection:
                    lineages = connection.execute(
                        sa.text(
                            "SELECT id, recruiter_opportunity_id, current_status, created_at, closed_at "
                            "FROM opportunity_lineages"
                        )
                    ).mappings().all()
                    self.assertEqual(len(lineages), 6)
                    by_opportunity = {
                        row["recruiter_opportunity_id"]: row
                        for row in lineages
                        if row["recruiter_opportunity_id"] is not None
                    }
                    self.assertEqual(set(by_opportunity), {501, 502, 503, 504, 505})
                    self.assertEqual(by_opportunity[503]["current_status"], "closed")
                    self.assertIsNone(by_opportunity[503]["closed_at"])

                    references = connection.execute(
                        sa.text(
                            "SELECT lineage_id, source_type, external_id "
                            "FROM opportunity_source_references"
                        )
                    ).mappings().all()
                    self.assertEqual(len(references), 5)
                    self.assertNotIn("None", {row["external_id"] for row in references})
                    self.assertNotIn(
                        by_opportunity[503]["id"],
                        {row["lineage_id"] for row in references},
                    )
                    dangling = [
                        row
                        for row in references
                        if row["lineage_id"] == by_opportunity[504]["id"]
                    ]
                    self.assertEqual(dangling[0]["external_id"], "999")

                    cards = connection.execute(
                        sa.text("SELECT id, lineage_id FROM number_review_queue ORDER BY id")
                    ).mappings().all()
                    self.assertIsNotNone(cards[0]["lineage_id"])
                    self.assertIsNone(cards[1]["lineage_id"])
                    pending_lineage = next(
                        row for row in lineages if row["id"] == cards[0]["lineage_id"]
                    )
                    self.assertEqual(
                        str(pending_lineage["created_at"]),
                        "2026-07-10 00:00:00.000000",
                    )
                    self.assertEqual(
                        connection.execute(
                            sa.text("SELECT COUNT(*) FROM opportunity_lifecycle_events")
                        ).scalar_one(),
                        6,
                    )
                engine.dispose()

                command.downgrade(config, "20260824_0030")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn(
                    "lineage_id",
                    {column["name"] for column in inspector.get_columns("number_review_queue")},
                )
                for table_name in (
                    "opportunity_lifecycle_events",
                    "opportunity_source_references",
                    "opportunity_lineages",
                ):
                    self.assertNotIn(table_name, inspector.get_table_names())
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
