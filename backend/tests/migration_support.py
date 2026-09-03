"""Helpers for tests that rebuild an older schema by hand.

These tests start from Base.metadata.create_all(), which always reflects the
*current* models, then strip the columns a migration is supposed to add. SQLite
refuses to drop a column that appears in a foreign key definition, so any column
constrained by migration 0050 has to lose its constraint first.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def drop_foreign_keys(engine: sa.Engine, table: str, *names: str) -> None:
    existing = {fk.get("name") for fk in sa.inspect(engine).get_foreign_keys(table)}
    pending = [name for name in names if name in existing]
    if not pending:
        return
    with engine.begin() as connection:
        operations = Operations(MigrationContext.configure(connection))
        with operations.batch_alter_table(table) as batch:
            for name in pending:
                batch.drop_constraint(name, type_="foreignkey")
