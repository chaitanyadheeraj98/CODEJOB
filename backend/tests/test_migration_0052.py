"""Migration 0052 adds role_source + role_canonical and touches no existing value.

Scope note, same as test_migration_0051: this runs against temporary SQLite, which
is sufficient for a column add but NOT for index DDL - SQLite has no btree
key-length limit, so an index Postgres would reject passes here silently. The
index this revision creates is over a bounded String(255), which is exactly why it
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


class RoleProvenanceMigrationTests(unittest.TestCase):
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

    def test_upgrade_adds_both_columns_and_the_index(self) -> None:
        config = _alembic_config()
        command.upgrade(config, "20260913_0051")
        command.upgrade(config, "head")

        inspector = sa.inspect(self.engine)
        columns = {column["name"] for column in inspector.get_columns("recruiter_emails")}
        self.assertIn("role_source", columns)
        self.assertIn("role_canonical", columns)
        self.assertIn(
            "ix_recruiter_emails_role_canonical",
            {index["name"] for index in inspector.get_indexes("recruiter_emails")},
        )

        command.downgrade(config, "20260913_0051")
        columns = {column["name"] for column in sa.inspect(self.engine).get_columns("recruiter_emails")}
        self.assertNotIn("role_source", columns)
        self.assertNotIn("role_canonical", columns)

    def test_existing_rows_keep_their_role_and_get_null_provenance(self) -> None:
        """The forward-only guarantee: the migration must not rewrite any value."""
        config = _alembic_config()
        command.upgrade(config, "20260913_0051")

        original_role = "Java Architect with AI experience<br /><br />Location: Austin"
        # Built from the schema at revision 0051 rather than a hardcoded column list:
        # the ORM cannot be used here (its model already carries the 0052 columns),
        # and raw SQL bypasses the Python-side defaults that make most NOT NULL
        # columns writable.
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
            self.assertIsNone(row.role_source, "legacy rows must read as unknown provenance")
            self.assertIsNone(row.role_canonical)


if __name__ == "__main__":
    unittest.main()
