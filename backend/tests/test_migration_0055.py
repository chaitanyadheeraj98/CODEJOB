"""Migration 0055 adds gate_error to both tables that carry gate_provider.

Scope note, as in test_migration_0052: this runs against temporary SQLite, which is
sufficient for a column add. This revision creates no index - deliberately, since
gate_error is a diagnostic column read by grouping queries, not a filter - so there
is no index DDL for SQLite to under-test.
"""

import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from app.config import settings
from app.models import RecruiterEmail

TABLES = ("recruiter_emails", "recent_run_skipped_items")


def _placeholder_for(column_type: object) -> object:
    if isinstance(column_type, (sa.DateTime, sa.Date)):
        return "2026-01-01 00:00:00"
    if isinstance(column_type, sa.Boolean):
        return 0
    if isinstance(column_type, (sa.Integer, sa.Float, sa.Numeric)):
        return 0
    return ""


def _alembic_config() -> Config:
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    return config


class GateErrorMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        database_path = Path(self._directory.name) / "migration.db"
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        previous = settings.database_url
        settings.database_url = self.database_url
        self.addCleanup(setattr, settings, "database_url", previous)
        self.engine = sa.create_engine(self.database_url)
        self.addCleanup(self.engine.dispose)

    def test_upgrade_adds_gate_error_to_both_tables_and_downgrade_removes_it(self) -> None:
        config = _alembic_config()
        command.upgrade(config, "20260916_0054")
        command.upgrade(config, "head")

        inspector = sa.inspect(self.engine)
        for table in TABLES:
            columns = {column["name"] for column in inspector.get_columns(table)}
            self.assertIn("gate_error", columns, table)
            self.assertNotIn(
                "ix_recruiter_emails_gate_error",
                {index["name"] for index in inspector.get_indexes(table)},
                "gate_error is for diagnosis, not filtering - it must stay unindexed",
            )

        command.downgrade(config, "20260916_0054")
        inspector = sa.inspect(self.engine)
        for table in TABLES:
            columns = {column["name"] for column in inspector.get_columns(table)}
            self.assertNotIn("gate_error", columns, table)

    def test_existing_rows_keep_their_provider_and_read_as_unknown_cause(self) -> None:
        """Forward-only: history predates the column, so it must read as NULL, not ''.

        The distinction matters for the diagnosis this column exists for - a NULL row
        is "we never recorded a cause", not "the call succeeded".
        """
        config = _alembic_config()
        command.upgrade(config, "20260916_0054")

        wanted = {
            "owner_id": "owner",
            "sender": "jane@example.com",
            "subject": "Subject",
            "body": "Body",
            "state": "needs_review",
            "gate_provider": "groq_fallback_taxonomy",
        }
        inspector = sa.inspect(self.engine)
        values: dict[str, object] = {}
        for column in inspector.get_columns("recruiter_emails"):
            name = column["name"]
            if name in wanted:
                values[name] = wanted[name]
            elif not column["nullable"] and column.get("default") is None and name != "id":
                values[name] = _placeholder_for(column["type"])
        placeholders = ", ".join(f":{name}" for name in values)
        with Session(self.engine) as db:
            db.execute(
                sa.text(f"INSERT INTO recruiter_emails ({', '.join(values)}) VALUES ({placeholders})"),
                values,
            )
            db.commit()

        command.upgrade(config, "head")

        with Session(self.engine) as db:
            row = db.query(RecruiterEmail).one()
            self.assertEqual(row.gate_provider, "groq_fallback_taxonomy")
            self.assertIsNone(row.gate_error)


if __name__ == "__main__":
    unittest.main()
