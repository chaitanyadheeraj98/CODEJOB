"""Report applications whose resume_asset_id points at a resume row that no longer
exists.

Migration 0050 deliberately leaves applications.resume_asset_id and
appts_applications.resume_asset_id unconstrained: both are NOT NULL, so a dangling
value cannot be nulled out - the row would have to be deleted or repointed, and
that is a decision for a human, not a migration. The delete guard in main.py
(_delete_resume) stops new orphans by refusing to delete a resume that an *active*
application still uses, but it permits deleting one used only by closed or
soft-deleted applications, which is exactly how an orphan appears.

Run this before considering a follow-up migration that adds those two foreign keys
with ondelete=RESTRICT. A clean report is the precondition; a dirty one lists the
rows to repoint or delete first.

Usage: python scripts/audit_orphan_resume_asset_ids.py
Exit code 0 when clean, 1 when orphans exist.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402

TABLES = ("applications", "appts_applications")


def main() -> int:
    orphans_found = False
    with SessionLocal() as db:
        for table in TABLES:
            rows = db.execute(
                text(
                    f"SELECT child.id, child.owner_id, child.resume_asset_id, child.status, "
                    f"child.deleted_at FROM {table} AS child "
                    "WHERE NOT EXISTS "
                    "(SELECT 1 FROM resume_assets AS parent WHERE parent.id = child.resume_asset_id) "
                    "ORDER BY child.id"
                )
            ).mappings().all()
            total = db.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            print(f"{table}: {len(rows)} orphan(s) of {total} row(s)")
            for row in rows:
                state = "deleted" if row["deleted_at"] else row["status"]
                print(
                    f"  id={row['id']} owner={row['owner_id']} "
                    f"resume_asset_id={row['resume_asset_id']} state={state}"
                )
            if rows:
                orphans_found = True

    if orphans_found:
        print(
            "\nRepoint or delete the rows above before adding the resume_asset_id "
            "foreign keys. Do not add them with orphans present."
        )
        return 1
    print("\nClean: resume_asset_id foreign keys can be added with ondelete=RESTRICT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
