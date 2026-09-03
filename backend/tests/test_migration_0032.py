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


class CandidateRecordMigrationTests(unittest.TestCase):
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
                    # 101: promoted via a RecruiterOpportunity (gmail).
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=101,
                        owner_id="default-owner",
                        source="gmail",
                        external_message_id="gmail-source-101",
                        gmail_received_at=datetime(2026, 7, 1),
                    )
                    # 102: never touched by review/opportunity - the "hard-rejected" case.
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=102,
                        owner_id="default-owner",
                        source="gmail",
                        external_message_id="gmail-source-102",
                        gmail_received_at=datetime(2026, 7, 2),
                    )
                    # 103: nvoids-bridged email matching ExternalOpportunity 301's post id.
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=103,
                        owner_id="default-owner",
                        source="nvoids",
                        external_message_id="nvoids:nvoids-301",
                        gmail_received_at=datetime(2026, 7, 3),
                    )
                    # 104: source for a pending review card.
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=104,
                        owner_id="default-owner",
                        source="gmail",
                        external_message_id="gmail-source-104",
                        gmail_received_at=datetime(2026, 7, 4),
                    )
                    # 105: source for a classified-but-never-lineaged review card.
                    self._insert(
                        connection,
                        "recruiter_emails",
                        id=105,
                        owner_id="default-owner",
                        source="gmail",
                        external_message_id="gmail-source-105",
                        gmail_received_at=datetime(2026, 7, 5),
                    )

                    self._insert(
                        connection, "external_feed_sources", id=201, owner_id="default-owner", source_type="nvoids"
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
                        posted_at=datetime(2026, 6, 25),
                    )
                    self._insert(
                        connection,
                        "external_opportunities",
                        id=302,
                        owner_id="default-owner",
                        feed_source_id=201,
                        source_type="nvoids",
                        external_post_id="nvoids-302",
                        dedupe_hash="nvoids-hash-302",
                        posted_at=datetime(2026, 6, 26),
                    )

                    for contact_id in range(401, 404):
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
                            "status": "New",
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
                        source_email_id=104,
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
                        source_external_opportunity_id=302,
                        normalized_phone_number="1555000602",
                        display_phone_number="+1 555 000 0602",
                        state="pending",
                        created_at=datetime(2026, 7, 11),
                    )
                    self._insert(
                        connection,
                        "number_review_queue",
                        id=603,
                        owner_id="default-owner",
                        source_email_id=105,
                        normalized_phone_number="1555000603",
                        display_phone_number="+1 555 000 0603",
                        state="classified_recruiter",
                        created_at=datetime(2026, 7, 12),
                    )
                engine.dispose()

                command.upgrade(config, "20260825_0031")
                command.upgrade(config, "20260826_0032")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                for table_name in (
                    "recruiter_emails",
                    "external_opportunities",
                    "number_review_queue",
                    "recruiter_opportunities",
                ):
                    self.assertIn("record_id", {column["name"] for column in inspector.get_columns(table_name)})
                self.assertIn("candidate_records", inspector.get_table_names())

                with engine.begin() as connection:
                    records = connection.execute(
                        sa.text("SELECT id, owner_id, origin_type, internal_lineage_id, created_at FROM candidate_records")
                    ).mappings().all()
                    records_by_id = {row["id"]: row for row in records}
                    self.assertEqual(len(records), 7)

                    emails = connection.execute(
                        sa.text("SELECT id, record_id FROM recruiter_emails ORDER BY id")
                    ).mappings().all()
                    email_record = {row["id"]: row["record_id"] for row in emails}
                    externals = connection.execute(
                        sa.text("SELECT id, record_id FROM external_opportunities ORDER BY id")
                    ).mappings().all()
                    external_record = {row["id"]: row["record_id"] for row in externals}
                    opportunities = connection.execute(
                        sa.text("SELECT id, record_id FROM recruiter_opportunities ORDER BY id")
                    ).mappings().all()
                    opportunity_record = {row["id"]: row["record_id"] for row in opportunities}
                    cards = connection.execute(
                        sa.text("SELECT id, record_id, lineage_id FROM number_review_queue ORDER BY id")
                    ).mappings().all()
                    card_record = {row["id"]: row for row in cards}

                    # Every source row and every opportunity/review card got a record - none left null.
                    self.assertTrue(all(value is not None for value in email_record.values()))
                    self.assertTrue(all(value is not None for value in external_record.values()))
                    self.assertTrue(all(value is not None for value in opportunity_record.values()))
                    self.assertTrue(all(row["record_id"] is not None for row in card_record.values()))

                    # Nvoids listing + its bridged email share one record.
                    self.assertEqual(email_record[103], external_record[301])

                    # A promoted opportunity's record is reused from its source row, not duplicated.
                    self.assertEqual(opportunity_record[501], email_record[101])
                    self.assertEqual(opportunity_record[502], external_record[301])

                    # A review card's record is reused from its source row too.
                    self.assertEqual(card_record[601]["record_id"], email_record[104])
                    self.assertEqual(card_record[602]["record_id"], external_record[302])
                    self.assertEqual(card_record[603]["record_id"], email_record[105])

                    # A hard-rejected email (no review/opportunity ever) gets a fresh record with
                    # no lineage and its own true historical timestamp.
                    hard_rejected = records_by_id[email_record[102]]
                    self.assertIsNone(hard_rejected["internal_lineage_id"])
                    self.assertEqual(str(hard_rejected["created_at"]), "2026-07-02 00:00:00.000000")

                    # Records reached via a promoted opportunity are linked to that opportunity's lineage.
                    promoted_101 = records_by_id[email_record[101]]
                    promoted_301 = records_by_id[external_record[301]]
                    promoted_503 = records_by_id[opportunity_record[503]]
                    self.assertIsNotNone(promoted_101["internal_lineage_id"])
                    self.assertIsNotNone(promoted_301["internal_lineage_id"])
                    self.assertIsNotNone(promoted_503["internal_lineage_id"])
                    # opportunity 503 had neither source id - its record was minted fresh, not reused.
                    self.assertNotIn(opportunity_record[503], set(email_record.values()) | set(external_record.values()))

                    # A pending review card's lineage (created by 20260825_0031's own backfill,
                    # before promotion) is linked onto the reused record too.
                    pending_card_101 = records_by_id[card_record[601]["record_id"]]
                    self.assertIsNotNone(pending_card_101["internal_lineage_id"])
                    self.assertEqual(pending_card_101["internal_lineage_id"], card_record[601]["lineage_id"])
                    pending_card_302 = records_by_id[card_record[602]["record_id"]]
                    self.assertIsNotNone(pending_card_302["internal_lineage_id"])
                    self.assertEqual(pending_card_302["internal_lineage_id"], card_record[602]["lineage_id"])

                    # 603 was already "classified_recruiter" pre-migration, so 20260825_0031 never
                    # gave it a lineage_id - its record correctly stays unlinked, not invented.
                    self.assertIsNone(card_record[603]["lineage_id"])
                    classified_603 = records_by_id[card_record[603]["record_id"]]
                    self.assertIsNone(classified_603["internal_lineage_id"])

                    # Every linked lineage is claimed by exactly one record (the unique constraint's intent).
                    linked_lineage_ids = [row["internal_lineage_id"] for row in records if row["internal_lineage_id"]]
                    self.assertEqual(len(linked_lineage_ids), len(set(linked_lineage_ids)))
                engine.dispose()

                command.downgrade(config, "20260825_0031")
                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                self.assertNotIn("candidate_records", inspector.get_table_names())
                for table_name in (
                    "recruiter_emails",
                    "external_opportunities",
                    "number_review_queue",
                    "recruiter_opportunities",
                ):
                    self.assertNotIn("record_id", {column["name"] for column in inspector.get_columns(table_name)})
                # 20260825_0031's own tables/data are untouched by this migration's downgrade.
                self.assertIn("opportunity_lineages", inspector.get_table_names())
                self.assertIn("lineage_id", {column["name"] for column in inspector.get_columns("number_review_queue")})
                with engine.begin() as connection:
                    self.assertEqual(
                        connection.execute(sa.text("SELECT COUNT(*) FROM opportunity_lineages")).scalar_one(),
                        5,
                    )
                engine.dispose()
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
