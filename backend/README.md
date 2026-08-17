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
