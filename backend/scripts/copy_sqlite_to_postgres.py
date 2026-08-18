"""Copy every application table from a SQLite snapshot into fresh PostgreSQL.

The source must be a consistent backup, not the SQLite file currently being
written by the application. The target schema must already be at Alembic head
and contain no application data. The copy runs in one target transaction.

Usage:
    python scripts/copy_sqlite_to_postgres.py \
        --source sqlite:///./data-copy/codejob.db \
        --target postgresql://codejob:<password>@localhost:5432/codejob
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import sys

import sqlalchemy as sa


EXCLUDED_TABLES = frozenset({"alembic_version"})


def _application_table_names(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - EXCLUDED_TABLES


def _reflect_application_metadata(engine: sa.Engine) -> sa.MetaData:
    metadata = sa.MetaData()
    names = sorted(_application_table_names(engine))
    metadata.reflect(bind=engine, only=names)
    return metadata


def _validate_schema_pair(
    source_metadata: sa.MetaData,
    target_metadata: sa.MetaData,
) -> None:
    source_names = set(source_metadata.tables)
    target_names = set(target_metadata.tables)
    if source_names != target_names:
        missing = sorted(source_names - target_names)
        extra = sorted(target_names - source_names)
        raise RuntimeError(
            f"Source/target table mismatch; target_missing={missing}, target_extra={extra}"
        )

    for table_name in sorted(source_names):
        source_columns = list(source_metadata.tables[table_name].columns.keys())
        target_columns = list(target_metadata.tables[table_name].columns.keys())
        if set(source_columns) != set(target_columns):
            missing = sorted(set(source_columns) - set(target_columns))
            extra = sorted(set(target_columns) - set(source_columns))
            raise RuntimeError(
                f"{table_name}: source/target column mismatch; "
                f"target_missing={missing}, target_extra={extra}"
            )


def _ensure_target_empty(
    target_metadata: sa.MetaData,
    target_connection: sa.Connection,
) -> None:
    nonempty = [
        table.name
        for table in target_metadata.sorted_tables
        if target_connection.execute(sa.select(sa.literal(1)).select_from(table).limit(1)).first()
        is not None
    ]
    if nonempty:
        raise RuntimeError(
            "Refusing to copy into a non-empty target; recreate the database first. "
            f"Non-empty tables: {', '.join(sorted(nonempty))}"
        )


def _validate_string_capacities(
    source_metadata: sa.MetaData,
    target_metadata: sa.MetaData,
    source_connection: sa.Connection,
) -> None:
    problems: list[str] = []
    for table_name in sorted(target_metadata.tables):
        source_table = source_metadata.tables[table_name]
        target_table = target_metadata.tables[table_name]
        bounded_columns = [
            target_column
            for target_column in target_table.columns
            if isinstance(target_column.type, sa.String)
            and getattr(target_column.type, "length", None) is not None
        ]
        if not bounded_columns:
            continue
        maxima = source_connection.execute(
            sa.select(
                *(
                    sa.func.max(sa.func.length(source_table.c[column.name]))
                    for column in bounded_columns
                )
            )
        ).one()
        for target_column, maximum in zip(bounded_columns, maxima, strict=True):
            target_length = getattr(target_column.type, "length", None)
            source_column = source_table.c[target_column.name]
            if maximum is None or int(maximum) <= int(target_length):
                continue
            primary_keys = list(source_table.primary_key.columns)
            sample = source_connection.execute(
                sa.select(*primary_keys, sa.func.length(source_column).label("value_length"))
                .where(source_column.is_not(None))
                .order_by(sa.func.length(source_column).desc())
                .limit(1)
            ).one()
            sample_pk = dict(zip((column.name for column in primary_keys), sample[:-1], strict=True))
            problems.append(
                f"{table_name}.{target_column.name}: max_length={maximum} "
                f"target_length={target_length} sample_pk={sample_pk}"
            )
    if problems:
        raise RuntimeError(
            "Source values exceed target VARCHAR capacities:\n" + "\n".join(problems)
        )


def _postgres_sequence_name(
    table: sa.Table,
    connection: sa.Connection,
) -> tuple[sa.Column, str] | None:
    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1 or not isinstance(primary_keys[0].type, sa.Integer):
        return None
    column = primary_keys[0]
    sequence_name = connection.execute(
        sa.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
        {"table_name": table.name, "column_name": column.name},
    ).scalar_one()
    if not sequence_name:
        raise RuntimeError(
            f"{table.name}.{column.name}: PostgreSQL primary key has no owned sequence"
        )
    return column, str(sequence_name)


def validate_postgres_sequences(
    target_metadata: sa.MetaData,
    target_connection: sa.Connection,
) -> dict[str, str]:
    """Return the owned sequence for every single-integer-primary-key table."""

    sequences: dict[str, str] = {}
    for table in target_metadata.sorted_tables:
        found = _postgres_sequence_name(table, target_connection)
        if found is not None:
            _, sequence_name = found
            sequences[table.name] = sequence_name
    return sequences


def copy_table(
    source_table: sa.Table,
    target_table: sa.Table,
    source_connection: sa.Connection,
    target_connection: sa.Connection,
    chunk_size: int,
) -> int:
    """Copy one reflected table in bounded batches using target-side types."""

    target_column_names = list(target_table.columns.keys())
    source_columns = [source_table.c[name] for name in target_column_names]
    statement = sa.select(*source_columns)
    source_primary_keys = [source_table.c[column.name] for column in source_table.primary_key]
    if source_primary_keys:
        statement = statement.order_by(*source_primary_keys)

    total = 0
    result = source_connection.execute(statement)
    while rows := result.fetchmany(chunk_size):
        payload = [
            dict(zip(target_column_names, tuple(row), strict=True))
            for row in rows
        ]
        target_connection.execute(sa.insert(target_table), payload)
        total += len(payload)
    return total


def _reset_postgres_sequence(
    table: sa.Table,
    connection: sa.Connection,
) -> None:
    found = _postgres_sequence_name(table, connection)
    if found is None:
        return
    column, sequence_name = found
    maximum = connection.execute(sa.select(sa.func.max(column))).scalar_one()
    connection.execute(
        sa.text("SELECT setval(CAST(:sequence_name AS regclass), :value, :is_called)"),
        {
            "sequence_name": sequence_name,
            "value": int(maximum) if maximum is not None else 1,
            "is_called": maximum is not None,
        },
    )


def copy_database(
    source_url: str,
    target_url: str,
    *,
    chunk_size: int = 500,
    require_postgresql_target: bool = True,
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, int]:
    """Validate and atomically copy a complete application database."""

    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    source_engine = sa.create_engine(source_url)
    target_engine = sa.create_engine(target_url)
    try:
        if source_engine.dialect.name != "sqlite":
            raise RuntimeError("The source must be SQLite")
        if require_postgresql_target and target_engine.dialect.name != "postgresql":
            raise RuntimeError("The target must be PostgreSQL")

        source_metadata = _reflect_application_metadata(source_engine)
        target_metadata = _reflect_application_metadata(target_engine)
        _validate_schema_pair(source_metadata, target_metadata)

        counts: dict[str, int] = {}
        with source_engine.connect() as source_connection, target_engine.begin() as target_connection:
            _ensure_target_empty(target_metadata, target_connection)
            _validate_string_capacities(
                source_metadata,
                target_metadata,
                source_connection,
            )
            if target_engine.dialect.name == "postgresql":
                validate_postgres_sequences(target_metadata, target_connection)

            for target_table in target_metadata.sorted_tables:
                source_table = source_metadata.tables[target_table.name]
                count = copy_table(
                    source_table,
                    target_table,
                    source_connection,
                    target_connection,
                    chunk_size,
                )
                counts[target_table.name] = count
                if progress is not None:
                    progress(target_table.name, count)

            if target_engine.dialect.name == "postgresql":
                for target_table in target_metadata.sorted_tables:
                    _reset_postgres_sequence(target_table, target_connection)
        return counts
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--chunk-size", type=int, default=500)
    args = parser.parse_args()

    copy_database(
        args.source,
        args.target,
        chunk_size=args.chunk_size,
        progress=lambda table, count: print(f"{table}: copied {count} rows"),
    )
    print("OK: complete copy committed and PostgreSQL sequences reset")
    return 0


if __name__ == "__main__":
    sys.exit(main())
