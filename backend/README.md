## Database Migrations (Alembic)

Run from `backend/`:

```bash
alembic upgrade head
alembic current
alembic history
```

Alembic is the only production schema writer. Application startup checks that the
database is already at the current head and fails with an actionable error when it
is not; it never calls `Base.metadata.create_all()` or patches SQLite with raw SQL.

Every schema change starts in the model and must include a hand-reviewed migration;
autogenerate is only an assist, and migrations should use guarded operations when
they must support an already-populated database. Validate migrations against both
supported database engines before deployment:

```bash
# Fresh SQLite
DATABASE_URL=sqlite:///./migration-check.db alembic upgrade head
DATABASE_URL=sqlite:///./migration-check.db python scripts/verify_schema_equivalence.py

# Fresh PostgreSQL
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/codejob_test alembic upgrade head
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/codejob_test python scripts/verify_schema_equivalence.py
```

The `Database migrations` GitHub Actions workflow runs these checks for migration,
model, database bootstrap, dependency, and verifier changes. Deployment remains
load-bearing: apply `alembic upgrade head` before starting the API or worker.

When running via `docker compose`, the backend command applies migrations before
starting Uvicorn. The worker waits for the backend service to start.

## SQLite to PostgreSQL cutover

The Compose deployment uses the internal `postgres` service and reads its local-only
credentials from `.env.postgres`. PostgreSQL is not published to a host port in the
final configuration. Before copying data, take an online SQLite backup and migrate a
fresh PostgreSQL database to Alembic head.

The copy tool reflects the complete migrated schema, including preserved legacy
tables and columns that are intentionally absent from the ORM. It requires an empty
target and performs the copy in one transaction, then resets every owned PostgreSQL
sequence. The parity tool compares both row counts and full, deterministic content
fingerprints:

```bash
python scripts/copy_sqlite_to_postgres.py --source <sqlite-backup-url> --target <postgres-url>
python scripts/verify_data_parity.py --source <sqlite-backup-url> --target <postgres-url>
python scripts/verify_schema_equivalence.py --database-url <postgres-url>
```

Never copy from the SQLite file while the application is writing to it, and never
reuse a PostgreSQL target containing parallel-test data. Recreate and migrate the
target, copy from a fresh stopped-app snapshot, verify parity, and only then start
the backend and worker against PostgreSQL. Keep the final SQLite backup and volume
untouched for rollback.
