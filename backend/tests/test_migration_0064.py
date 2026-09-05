"""Migration 0064: the preserved legacy columns stop blocking every INSERT.

The bug this pins is not theoretical - it was found on the local dev volume on
2026-09-04, where the backend could not boot. `20260817_0018` preserves a set of
retired resume-matching columns and gives them server defaults *"so ORM inserts
stay valid"*, but 0018 only creates tables **missing** from historical
migrations. A database that already had those columns kept them exactly as they
were: NOT NULL with no default. The ORM does not know they exist, so every
INSERT it builds omits them and the database rejects it - on `user_settings`
(which fails `ensure_default_settings()` during startup, so the process exits),
and equally on `recruiter_emails` and `resume_assets`.

Each test here builds that drifted shape from scratch rather than reading any
particular file, so the repair is pinned independently of the volume that
exposed it.
"""

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
import sqlalchemy.orm
from alembic import command
from alembic.config import Config

import app.models  # noqa: F401  - populates Base.metadata
from app.config import settings
from app.db import Base

PREVIOUS_REVISION = "20260904_0063"

# Exactly the NOT NULL half of the preserved set, declared the way the historical
# volume carries it: no default, so an omitted INSERT is rejected.
LEGACY_NOT_NULL: dict[str, tuple[str, ...]] = {
    "user_settings": ("feature_resume_matching_enabled BOOLEAN NOT NULL",),
    "resume_assets": (
        "normalized_skills_json TEXT NOT NULL",
        "skills_extraction_status VARCHAR(40) NOT NULL",
        "evidence_profile_json TEXT NOT NULL",
        "evidence_extraction_status VARCHAR(40) NOT NULL",
        "profile_version VARCHAR(40) NOT NULL",
    ),
    "recruiter_emails": (
        "evidence_summary_json TEXT NOT NULL",
        "missing_requirements_json TEXT NOT NULL",
        "risk_flags_json TEXT NOT NULL",
    ),
}


@contextmanager
def scratch_directory():
    """mkdtemp rather than TemporaryDirectory.

    SQLite on Windows keeps the file handle open until every engine alembic
    created is garbage-collected, and TemporaryDirectory's cleanup raises
    PermissionError when it is not.
    """
    directory = tempfile.mkdtemp()
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _add_legacy_columns(database_path: Path) -> None:
    """Rewrite the three tables so they carry the legacy columns with no default.

    SQLite refuses `ADD COLUMN ... NOT NULL` without a default - which is the
    whole point - so each table is recreated from its own DDL with the extra
    columns spliced in. The tables are empty here, so no rows need copying.
    """
    connection = sqlite3.connect(database_path)
    for table, extra_columns in LEGACY_NOT_NULL.items():
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        indexes = [
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
                (table,),
            )
        ]
        # Spliced at the *head* of the column list, not the tail: the DDL ends
        # with a table constraint ("PRIMARY KEY (id)"), and a column definition
        # after one is a syntax error. Column order is irrelevant here.
        opening = ddl.index("(")
        columns = ",\n\t".join(extra_columns)
        rebuilt = f"{ddl[: opening + 1]}\n\t{columns},{ddl[opening + 1 :]}"
        connection.execute(f"DROP TABLE {table}")
        connection.execute(rebuilt)
        for index_sql in indexes:
            connection.execute(index_sql)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute(
        "INSERT INTO alembic_version(version_num) VALUES (?)", (PREVIOUS_REVISION,)
    )
    connection.commit()
    connection.close()


class LegacyColumnDefaultTests(unittest.TestCase):
    def _alembic_config(self) -> Config:
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
        return config

    def _insert_probe(self, database_url: str) -> dict[str, str | None]:
        """Try the three inserts the drift breaks. None means the insert worked."""
        engine = sa.create_engine(database_url)
        results: dict[str, str | None] = {}
        try:
            for label, build in (
                ("user_settings", lambda: app.models.UserSettings(owner_id="probe")),
                (
                    "recruiter_emails",
                    lambda: app.models.RecruiterEmail(
                        owner_id="probe", sender="a@b.c", subject="s", body="b"
                    ),
                ),
                (
                    "resume_assets",
                    lambda: app.models.ResumeAsset(
                        owner_id="probe", file_path="p", file_name="r.pdf", sha256="0" * 64
                    ),
                ),
            ):
                with sa.orm.Session(engine) as session:
                    try:
                        session.add(build())
                        session.commit()
                        results[label] = None
                    except Exception as exc:  # noqa: BLE001 - the message is the assertion
                        results[label] = str(exc).splitlines()[0]
                    finally:
                        session.rollback()
        finally:
            engine.dispose()
        return results

    def _drifted_database(self, directory: str) -> str:
        database_path = Path(directory) / "drifted.db"
        database_url = f"sqlite:///{database_path.as_posix()}"
        engine = sa.create_engine(database_url)
        Base.metadata.create_all(engine)
        engine.dispose()
        _add_legacy_columns(database_path)
        return database_url

    def _defaults(self, database_url: str, table: str) -> dict[str, str | None]:
        engine = sa.create_engine(database_url)
        try:
            return {row["name"]: row["default"] for row in sa.inspect(engine).get_columns(table)}
        finally:
            engine.dispose()

    def _indexes(self, database_url: str, table: str) -> set[str]:
        engine = sa.create_engine(database_url)
        try:
            return {index["name"] for index in sa.inspect(engine).get_indexes(table)}
        finally:
            engine.dispose()

    # --- the bug ---------------------------------------------------------

    def test_the_drift_reproduces_the_failure_on_all_three_tables(self) -> None:
        """Guards the guard: if this stops failing, the repair test proves nothing."""
        with scratch_directory() as directory:
            database_url = self._drifted_database(directory)
            failures = self._insert_probe(database_url)
            self.assertIn("feature_resume_matching_enabled", failures["user_settings"] or "")
            self.assertIn("evidence_summary_json", failures["recruiter_emails"] or "")
            self.assertIn("normalized_skills_json", failures["resume_assets"] or "")

    # --- the repair ------------------------------------------------------

    def test_0064_makes_every_blocked_insert_work(self) -> None:
        with scratch_directory() as directory:
            database_url = self._drifted_database(directory)
            before = {table: self._indexes(database_url, table) for table in LEGACY_NOT_NULL}
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")

                self.assertEqual(
                    self._insert_probe(database_url),
                    {"user_settings": None, "recruiter_emails": None, "resume_assets": None},
                )
                for table in LEGACY_NOT_NULL:
                    # Batch mode rebuilds the table on SQLite; recruiter_emails
                    # alone carries 28 indexes, and losing one would be a silent
                    # full-table-scan regression rather than an error.
                    self.assertEqual(self._indexes(database_url, table), before[table], table)
            finally:
                settings.database_url = previous_url

    def test_the_columns_are_kept_not_dropped(self) -> None:
        """0018 preserves this set on purpose. 0064 makes it insertable, nothing more."""
        with scratch_directory() as directory:
            database_url = self._drifted_database(directory)
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                for table, columns in LEGACY_NOT_NULL.items():
                    defaults = self._defaults(database_url, table)
                    for declaration in columns:
                        name = declaration.split()[0]
                        self.assertIn(name, defaults, f"{table}.{name} was dropped")
                        self.assertIsNotNone(defaults[name], f"{table}.{name} still has no default")
            finally:
                settings.database_url = previous_url

    def test_re_running_upgrade_head_is_a_no_op(self) -> None:
        """Boot runs `upgrade head` every time, and batch mode rebuilds tables."""
        with scratch_directory() as directory:
            database_url = self._drifted_database(directory)
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                command.upgrade(config, "head")
                command.downgrade(config, PREVIOUS_REVISION)
                command.upgrade(config, "head")
                self.assertEqual(
                    self._insert_probe(database_url),
                    {"user_settings": None, "recruiter_emails": None, "resume_assets": None},
                )
            finally:
                settings.database_url = previous_url

    def test_a_database_without_the_legacy_columns_is_untouched(self) -> None:
        """The common case: nothing to repair, so no table is rebuilt."""
        with scratch_directory() as directory:
            database_path = Path(directory) / "clean.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            engine = sa.create_engine(database_url)
            Base.metadata.create_all(engine)
            engine.dispose()
            connection = sqlite3.connect(database_path)
            connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
            connection.execute(
                "INSERT INTO alembic_version(version_num) VALUES (?)", (PREVIOUS_REVISION,)
            )
            connection.commit()
            connection.close()

            before = {
                table: self._defaults(database_url, table) for table in LEGACY_NOT_NULL
            }
            config = self._alembic_config()
            previous_url = settings.database_url
            try:
                settings.database_url = database_url
                command.upgrade(config, "head")
                for table in LEGACY_NOT_NULL:
                    self.assertEqual(self._defaults(database_url, table), before[table], table)
                self.assertEqual(
                    self._insert_probe(database_url),
                    {"user_settings": None, "recruiter_emails": None, "resume_assets": None},
                )
            finally:
                settings.database_url = previous_url


if __name__ == "__main__":
    unittest.main()
