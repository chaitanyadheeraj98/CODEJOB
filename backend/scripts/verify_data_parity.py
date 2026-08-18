"""Compare row counts and complete content fingerprints across two databases.

Usage:
    python scripts/verify_data_parity.py --source <sqlite-url> --target <postgres-url>
"""

from __future__ import annotations

import argparse
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import math
from pathlib import Path
import struct
import sys
from typing import Any

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.copy_sqlite_to_postgres import (
    _reflect_application_metadata,
    _validate_schema_pair,
)


def _canonical_bytes(value: Any) -> bytes:
    if value is None:
        return b"n"
    if isinstance(value, bool):
        return b"b1" if value else b"b0"
    if isinstance(value, int):
        return b"i" + str(value).encode("ascii")
    if isinstance(value, float):
        if math.isnan(value):
            return b"fNaN"
        return b"f" + struct.pack("!d", value)
    if isinstance(value, Decimal):
        return b"d" + format(value, "f").encode("ascii")
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return b"t" + value.isoformat(timespec="microseconds").encode("ascii")
    if isinstance(value, date):
        return b"D" + value.isoformat().encode("ascii")
    if isinstance(value, bytes):
        return b"x" + value
    if isinstance(value, str):
        return b"s" + value.encode("utf-8")
    raise TypeError(f"Unsupported value type for parity fingerprint: {type(value)!r}")


def _table_fingerprint(
    table: sa.Table,
    connection: sa.Connection,
    column_names: list[str],
    chunk_size: int,
) -> tuple[int, str]:
    columns = [table.c[name] for name in column_names]
    primary_keys = [table.c[column.name] for column in table.primary_key]
    if not primary_keys:
        raise RuntimeError(f"{table.name}: parity verification requires a primary key")

    result = connection.execute(sa.select(*columns).order_by(*primary_keys))
    digest = hashlib.sha256()
    count = 0
    while rows := result.fetchmany(chunk_size):
        for row in rows:
            for value in tuple(row):
                encoded = _canonical_bytes(value)
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
            digest.update(b"\xff")
            count += 1
    return count, digest.hexdigest()


def compare_data(
    source_url: str,
    target_url: str,
    *,
    chunk_size: int = 500,
) -> tuple[list[str], dict[str, tuple[int, str]]]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")

    source_engine = sa.create_engine(source_url)
    target_engine = sa.create_engine(target_url)
    try:
        source_metadata = _reflect_application_metadata(source_engine)
        target_metadata = _reflect_application_metadata(target_engine)
        try:
            _validate_schema_pair(source_metadata, target_metadata)
        except RuntimeError as error:
            return [str(error)], {}

        problems: list[str] = []
        results: dict[str, tuple[int, str]] = {}
        with source_engine.connect() as source_connection, target_engine.connect() as target_connection:
            for table_name in sorted(source_metadata.tables):
                source_table = source_metadata.tables[table_name]
                target_table = target_metadata.tables[table_name]
                column_names = list(target_table.columns.keys())
                source_count, source_digest = _table_fingerprint(
                    source_table,
                    source_connection,
                    column_names,
                    chunk_size,
                )
                target_count, target_digest = _table_fingerprint(
                    target_table,
                    target_connection,
                    column_names,
                    chunk_size,
                )
                results[table_name] = (target_count, target_digest)
                if source_count != target_count:
                    problems.append(
                        f"{table_name}: ROW COUNT MISMATCH "
                        f"source={source_count} target={target_count}"
                    )
                elif source_digest != target_digest:
                    problems.append(
                        f"{table_name}: CONTENT MISMATCH rows={source_count} "
                        f"source_sha256={source_digest} target_sha256={target_digest}"
                    )
        return problems, results
    finally:
        source_engine.dispose()
        target_engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--chunk-size", type=int, default=500)
    args = parser.parse_args()

    problems, results = compare_data(
        args.source,
        args.target,
        chunk_size=args.chunk_size,
    )
    if problems:
        for problem in problems:
            print("MISMATCH:", problem)
        print(f"\n{len(problems)} table(s) mismatched")
        return 1
    for table_name, (count, digest) in results.items():
        print(f"{table_name}: rows={count} sha256={digest}")
    print("OK: row counts and content fingerprints match on every table")
    return 0


if __name__ == "__main__":
    sys.exit(main())
