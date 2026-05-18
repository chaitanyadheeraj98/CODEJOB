## Database Migrations (Alembic)

Run from `backend/`:

```bash
alembic upgrade head
alembic current
alembic history
```

Current branch strict mode sets `ALLOW_RUNTIME_SCHEMA_PATCH=false` and requires migrations
to be applied before startup.

When running via `docker compose`, backend startup now runs `alembic upgrade head`
before `uvicorn` so fresh/behind SQLite volumes are migrated automatically.
