# Taxonomy data ownership

`app/data/skill_taxonomy.json` is the reviewed, version-controlled seed. Approved
rows in `custom_skill_taxonomy_entries` are the live, owner-specific overlay. The
loader merges the overlay into the seed by normalized canonical name; it does not
replace the seed file.

Schema changes are owned by Alembic from revision `20260801_0012`. The SQLite
startup compatibility checks remain only for older local databases.

Master datasets are imported explicitly and offline. Run a collision-only dry run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.taxonomy_import path\to\pinned-skills.json
```

Review the reported collisions, then repeat with `--apply`. The application never
downloads or auto-promotes a third-party taxonomy at runtime.
