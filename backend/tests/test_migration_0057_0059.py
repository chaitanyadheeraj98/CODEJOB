"""The three v3 migrations: upgrade, re-upgrade, downgrade, re-upgrade.

All three run on backend boot via `alembic upgrade head`. Migration 0051 took
production down with an index over an unbounded Text column, which is why every
index here is asserted to be on a fixed-width or FK column and why the Text
columns are named explicitly rather than checked by shape.

The downgrade/re-upgrade cycle is not ceremony: a migration that cannot be
reversed cannot be backed out of a boot loop, and a boot loop is exactly what
0051 caused.
"""

import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from app.config import settings
from app.db import Base
# Imported for the side effect: without it Base.metadata is empty when this
# file runs alone, create_all() makes no tables, and the DROPs below fail.
import app.models  # noqa: F401

TABLES = (
    "relationship_labels",
    "opportunity_clusters",
    "opportunity_cluster_members",
    "relationship_judgments",
)

# Text on the model, and never indexable.
TEXT_COLUMNS = {"reason", "evidence_json", "correction_json", "note"}

EXPECTED_INDEXED = {
    "relationship_labels": {
        "id", "owner_id", "left_opportunity_id", "right_opportunity_id", "verdict", "split", "sampler",
    },
    "opportunity_clusters": {"owner_id", "confidence", "status", "semantic_available", "member_key"},
    "opportunity_cluster_members": {"id", "owner_id", "cluster_id", "opportunity_id", "confidence"},
    "relationship_judgments": {"id", "owner_id", "subject_type", "subject_id", "verdict", "suppression_key"},
}


class RelationshipMigrationTests(unittest.TestCase):
    def indexed_columns(self, inspector: sa.Inspector, table: str) -> set[str]:
        return {
            column
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
            if column is not None
        }

    def test_the_three_revisions_upgrade_downgrade_and_re_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "migration.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            with engine.begin() as connection:
                for table in ("opportunity_cluster_members", "opportunity_clusters", "relationship_labels", "relationship_judgments"):
                    connection.exec_driver_sql(f"DROP TABLE {table}")
                connection.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
                connection.exec_driver_sql("INSERT INTO alembic_version(version_num) VALUES ('20260918_0056')")
            engine.dispose()

            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")

                engine = sa.create_engine(database_url)
                inspector = sa.inspect(engine)
                names = set(inspector.get_table_names())
                for table in TABLES:
                    self.assertIn(table, names)
                    indexed = self.indexed_columns(inspector, table)
                    self.assertEqual(indexed, EXPECTED_INDEXED[table], table)
                    # 0051's failure mode, pinned per table.
                    self.assertFalse(indexed & TEXT_COLUMNS, table)
                engine.dispose()

                # Boot runs `upgrade head` every time, so it must be idempotent.
                command.upgrade(config, "head")

                command.downgrade(config, "20260918_0056")
                engine = sa.create_engine(database_url)
                remaining = set(sa.inspect(engine).get_table_names())
                for table in TABLES:
                    self.assertNotIn(table, remaining)
                engine.dispose()

                # A migration that cannot be re-applied after a downgrade
                # cannot be backed out of a boot loop and then rolled forward.
                command.upgrade(config, "head")
                engine = sa.create_engine(database_url)
                restored = set(sa.inspect(engine).get_table_names())
                for table in TABLES:
                    self.assertIn(table, restored)
                engine.dispose()
            finally:
                settings.database_url = previous_url

    def test_a_rejected_member_set_is_suppressed_by_a_fixed_width_column(self) -> None:
        # suppression_key exists precisely so that suppression never requires a
        # query over correction_json, which is Text and must never be indexed.
        columns = {column.name: column for column in app.models.RelationshipJudgment.__table__.columns}
        self.assertIsInstance(columns["suppression_key"].type, sa.String)
        self.assertEqual(columns["suppression_key"].type.length, 64)
        self.assertTrue(columns["suppression_key"].index)
        self.assertIsInstance(columns["correction_json"].type, sa.Text)
        self.assertFalse(columns["correction_json"].index)


if __name__ == "__main__":
    unittest.main()
