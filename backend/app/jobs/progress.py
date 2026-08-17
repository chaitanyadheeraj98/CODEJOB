from __future__ import annotations

from rq import get_current_job
from sqlalchemy.orm import Session

from app.models import RecentRun


def update_job_progress(
    db: Session,
    *,
    run_key: str,
    processed_items: int | None = None,
    total_items: int | None = None,
    status: str | None = None,
    detail: str | None = None,
    complete: bool = False,
) -> RecentRun:
    row = db.query(RecentRun).filter(RecentRun.run_key == run_key).first()
    if row is None:
        raise LookupError(f"Recent run not found: {run_key}")

    processed = max(int(row.processed_items or 0), int(processed_items or 0))
    total = max(int(row.total_items or 0), int(total_items or 0), processed)
    row.processed_items = processed
    row.total_items = total
    if complete:
        row.progress_pct = 100.0
    elif total > 0:
        row.progress_pct = min(99.0, round((processed / total) * 100.0, 2))
    elif row.progress_pct is None:
        row.progress_pct = 0.0
    if status is not None:
        row.status = status
    if detail is not None:
        row.detail = detail
    db.commit()
    db.refresh(row)

    job = get_current_job()
    if job is not None:
        job.meta.update(
            {
                "run_key": run_key,
                "processed_items": row.processed_items,
                "total_items": row.total_items,
                "progress_pct": row.progress_pct,
                "status": row.status,
            }
        )
        job.save_meta()
    return row

