import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa

from scripts.copy_sqlite_to_postgres import copy_database
from scripts.verify_data_parity import compare_data


def _fixture_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "parents",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
    )
    sa.Table(
        "children",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("parents.id"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
    )
    return metadata


class CopyScriptTests(unittest.TestCase):
    def test_reflected_copy_preserves_types_dependencies_and_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_url = f"sqlite:///{(Path(directory) / 'source.db').as_posix()}"
            target_url = f"sqlite:///{(Path(directory) / 'target.db').as_posix()}"
            source_engine = sa.create_engine(source_url)
            target_engine = sa.create_engine(target_url)
            metadata = _fixture_metadata()
            metadata.create_all(source_engine)
            metadata.create_all(target_engine)
            observed_at = datetime(2026, 8, 17, 20, 15, 42, 123456)
            with source_engine.begin() as connection:
                connection.execute(
                    metadata.tables["parents"].insert(),
                    [
                        {
                            "id": 7,
                            "enabled": True,
                            "observed_at": observed_at,
                            "payload_json": '{"skills":["Python","PostgreSQL"]}',
                        },
                        {
                            "id": 9,
                            "enabled": False,
                            "observed_at": observed_at,
                            "payload_json": "{}",
                        },
                    ],
                )
                connection.execute(
                    metadata.tables["children"].insert(),
                    [{"id": 11, "parent_id": 7, "note": "legacy column data"}],
                )
            source_engine.dispose()
            target_engine.dispose()

            progress: list[tuple[str, int]] = []
            counts = copy_database(
                source_url,
                target_url,
                chunk_size=1,
                require_postgresql_target=False,
                progress=lambda table, count: progress.append((table, count)),
            )

            self.assertEqual(counts, {"parents": 2, "children": 1})
            self.assertEqual(progress, [("parents", 2), ("children", 1)])
            target_engine = sa.create_engine(target_url)
            reflected = sa.MetaData()
            reflected.reflect(target_engine)
            with target_engine.connect() as connection:
                row = connection.execute(
                    sa.select(reflected.tables["parents"]).where(
                        reflected.tables["parents"].c.id == 7
                    )
                ).one()
                self.assertIs(row.enabled, True)
                self.assertEqual(row.observed_at, observed_at)
                self.assertEqual(row.payload_json, '{"skills":["Python","PostgreSQL"]}')
            target_engine.dispose()

            problems, results = compare_data(source_url, target_url, chunk_size=1)
            self.assertEqual(problems, [])
            self.assertEqual(results["parents"][0], 2)

            with self.assertRaisesRegex(RuntimeError, "non-empty target"):
                copy_database(
                    source_url,
                    target_url,
                    require_postgresql_target=False,
                )

    def test_parity_detects_same_count_content_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_url = f"sqlite:///{(Path(directory) / 'source.db').as_posix()}"
            target_url = f"sqlite:///{(Path(directory) / 'target.db').as_posix()}"
            metadata = _fixture_metadata()
            source_engine = sa.create_engine(source_url)
            target_engine = sa.create_engine(target_url)
            metadata.create_all(source_engine)
            metadata.create_all(target_engine)
            row = {
                "id": 1,
                "enabled": True,
                "observed_at": datetime(2026, 8, 17, 20, 15, 42),
                "payload_json": "{}",
            }
            with source_engine.begin() as connection:
                connection.execute(metadata.tables["parents"].insert(), row)
            with target_engine.begin() as connection:
                connection.execute(
                    metadata.tables["parents"].insert(),
                    {**row, "payload_json": '{"changed":true}'},
                )
            source_engine.dispose()
            target_engine.dispose()

            problems, _ = compare_data(source_url, target_url)
            self.assertEqual(len(problems), 1)
            self.assertIn("parents: CONTENT MISMATCH", problems[0])

    def test_copy_reports_all_values_that_exceed_target_varchar_capacity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source_url = f"sqlite:///{(Path(directory) / 'source.db').as_posix()}"
            target_url = f"sqlite:///{(Path(directory) / 'target.db').as_posix()}"
            source_metadata = sa.MetaData()
            target_metadata = sa.MetaData()
            source_table = sa.Table(
                "items",
                source_metadata,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("name", sa.Text(), nullable=False),
            )
            sa.Table(
                "items",
                target_metadata,
                sa.Column("id", sa.Integer(), primary_key=True),
                sa.Column("name", sa.String(5), nullable=False),
            )
            source_engine = sa.create_engine(source_url)
            target_engine = sa.create_engine(target_url)
            source_metadata.create_all(source_engine)
            target_metadata.create_all(target_engine)
            with source_engine.begin() as connection:
                connection.execute(source_table.insert(), {"id": 3, "name": "too-long"})
            source_engine.dispose()
            target_engine.dispose()

            with self.assertRaisesRegex(
                RuntimeError,
                r"items\.name: max_length=8 target_length=5 sample_pk=\{'id': 3\}",
            ):
                copy_database(
                    source_url,
                    target_url,
                    require_postgresql_target=False,
                )


if __name__ == "__main__":
    unittest.main()
