from __future__ import annotations

import logging
from typing import Any

from app.db import SessionLocal
from app.jobs.progress import update_job_progress
from app.schemas import AutomationRunRequest

logger = logging.getLogger(__name__)


def _mark_failed(run_key: str, exc: Exception) -> None:
    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            status="failed",
            detail=f"Background job failed: {exc}",
        )
    except Exception:
        db.rollback()
        logger.exception("job_failure_status_update_failed run_key=%r", run_key)
    finally:
        db.close()


def run_gmail_sync_job(*, run_key: str, sync_batch_id: str) -> dict[str, Any]:
    from app import main

    db = SessionLocal()
    try:
        update_job_progress(db, run_key=run_key, status="running", detail="Gmail sync worker started.")
        response = main._run_gmail_sync(
            db,
            sync_batch_id=sync_batch_id,
            progress_callback=lambda processed, total: update_job_progress(
                db,
                run_key=run_key,
                processed_items=processed,
                total_items=total,
                status="running",
            ),
        )
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=max(response.imported_count + response.skipped_count + response.error_count, 0),
            status="ok",
            detail=(
                "Gmail sync complete: "
                f"imported={response.imported_count} skipped={response.skipped_count} "
                f"errors={response.error_count}"
            ),
            complete=True,
        )
        return {"run_key": run_key, "status": row.status}
    except Exception as exc:
        db.rollback()
        _mark_failed(run_key, exc)
        raise
    finally:
        db.close()


def run_nvoids_sync_job(*, run_key: str, max_items: int) -> dict[str, Any]:
    from app import main

    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            total_items=max_items,
            status="running",
            detail="Nvoids sync worker started.",
        )
        result = main._run_nvoids_sync(
            db,
            max_items=max_items,
            run_key_override=run_key,
            progress_callback=lambda processed, total: update_job_progress(
                db,
                run_key=run_key,
                processed_items=processed,
                total_items=total,
                status="running",
            ),
        )
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=max(result.fetched_count, result.created_count + result.deduped_count),
            status="ok",
            detail=(
                "nvoids sync complete: "
                f"fetched={result.fetched_count} created={result.created_count} "
                f"deduped={result.deduped_count} skipped_location={result.skipped_location_count} "
                f"failed={result.failed_count}"
            ),
            complete=True,
        )
        return {"run_key": run_key, "status": row.status}
    except Exception as exc:
        db.rollback()
        _mark_failed(run_key, exc)
        raise
    finally:
        db.close()


def run_automation_job(*, run_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    from app import main

    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            total_items=1,
            status="running",
            detail="Automation worker started.",
        )
        request = AutomationRunRequest.model_validate(payload) if payload else None
        response = main._run_automation(
            request,
            db,
            run_key_override=run_key,
        )
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=1,
            total_items=1,
            status=response.status,
            detail=response.detail,
            complete=True,
        )
        return {"run_key": run_key, "status": row.status}
    except Exception as exc:
        db.rollback()
        _mark_failed(run_key, exc)
        raise
    finally:
        db.close()
