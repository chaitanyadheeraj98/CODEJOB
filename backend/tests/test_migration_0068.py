"""Migration 0068 adds the three role-family columns and touches no existing value.

Scope note, same as test_migration_0052: this runs against temporary SQLite, which
is sufficient for a column add but NOT for index DDL - SQLite has no btree
key-length limit, so an index Postgres would reject passes here silently. The
index this revision creates is over a bounded String(40), which is exactly why it
is safe; an index over the unbounded Text column `role` is what took the backend
down previously.
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

PREVIOUS_REVISION = "20260906_0067"


def _placeholder_for(column_type: object) -> object:
    """A writable value for a NOT NULL column whose default lives in Python."""
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


class RoleFamilyMigrationTests(unittest.TestCase):
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

    def test_upgrade_adds_all_three_columns_and_the_index(self) -> None:
        config = _alembic_config()
        command.upgrade(config, PREVIOUS_REVISION)
        command.upgrade(config, "head")

        inspector = sa.inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("recruiter_emails")}
        self.assertIn("role_family", columns)
        self.assertIn("role_family_confidence", columns)
        self.assertIn("role_family_taxonomy_version", columns)
        self.assertIn(
            "ix_recruiter_emails_role_family",
            {index["name"] for index in inspector.get_indexes("recruiter_emails")},
        )

        command.downgrade(config, PREVIOUS_REVISION)
        inspector = sa.inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("recruiter_emails")}
        self.assertNotIn("role_family", columns)
        self.assertNotIn("role_family_confidence", columns)
        self.assertNotIn("role_family_taxonomy_version", columns)
        self.assertNotIn(
            "ix_recruiter_emails_role_family",
            {index["name"] for index in inspector.get_indexes("recruiter_emails")},
        )

    def test_existing_rows_keep_their_role_and_read_as_never_classified(self) -> None:
        """The forward-only guarantee: no backfill runs inside the migration.

        NULL here is load-bearing - it means "never classified" and must not be
        confused with a genuine `general` classification, which is what the
        separate backfill script writes.
        """
        config = _alembic_config()
        command.upgrade(config, PREVIOUS_REVISION)

        original_role = "IT Security Auditor"
        # Built from the schema at the previous revision rather than a hardcoded
        # column list: the ORM cannot be used here (its model already carries the
        # 0061 columns), and raw SQL bypasses the Python-side defaults that make
        # most NOT NULL columns writable.
        wanted = {
            "owner_id": "owner", "sender": "jane@example.com", "subject": "Subject",
            "body": "Body", "role": original_role, "location": "Austin", "state": "needs_review",
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
            self.assertEqual(row.role, original_role, "migration must not rewrite existing roles")
            self.assertIsNone(row.role_family, "legacy rows must read as never classified")
            self.assertIsNone(row.role_family_confidence)
            self.assertIsNone(row.role_family_taxonomy_version)


if __name__ == "__main__":
    unittest.main()
