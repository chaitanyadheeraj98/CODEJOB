from __future__ import annotations

import logging
import json
from typing import Any

from app.db import SessionLocal
from app.jobs.progress import update_job_progress
from app.schemas import AutomationRunRequest

logger = logging.getLogger(__name__)


def run_generate_embedding_job(*, record_type: str, record_id: int) -> dict[str, Any]:
    from app.config import settings
    from app.models import AppTSApplication, RecruiterEmail
    from app.semantic.embeddings_service import generate_embedding

    model_cls = RecruiterEmail if record_type == "recruiter_email" else AppTSApplication
    db = SessionLocal()
    try:
        row = db.query(model_cls).filter(model_cls.id == record_id).first()
        if row is None:
            return {"status": "skipped", "reason": "record_not_found"}
        role = row.role if record_type == "recruiter_email" else row.job_title_snapshot
        skills = row.skills_text if record_type == "recruiter_email" else row.resume_skills_snapshot_json
        text = f"{role or ''} {skills or ''}".strip()
        if not text:
            return {"status": "skipped", "reason": "no_text"}
        vector, provider = generate_embedding(text)
        if record_type == "recruiter_email":
            row.semantic_embedding = json.dumps(vector)
        else:
            row.embedding = json.dumps(vector)
        row.embedding_model = settings.semantic_embedding_sbert_model if provider == "sbert" else provider
        db.commit()
        return {"status": "ok", "provider": provider}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_scheduled_task_job(*, task_id: int) -> dict[str, Any]:
    """Execute one scheduled task run.

    The draft-and-hold boundary as a code-level rule: this function and the
    handler it dispatches to write **only** `scheduled_tasks` and
    `scheduled_task_runs`. A test snapshots every other table's row count around
    a run, so an edit that breaks the rule fails CI rather than reaching a user.

    RQ's Retry is used elsewhere in this codebase but not here. A scheduled task
    that fails should surface, and the next sweep tick is the natural retry.
    """
    from app.models import SUPERSEDING_KINDS, ScheduledTask, ScheduledTaskRun, utc_now
    from app.services.scheduling.handlers import handler_for

    db = SessionLocal()
    try:
        task = db.get(ScheduledTask, task_id)
        if task is None:
            return {"status": "skipped", "reason": "task_not_found"}
        if task.status != "active":
            return {"status": "skipped", "reason": f"task_{task.status}"}

        run = ScheduledTaskRun(
            owner_id=task.owner_id,
            task_id=task.id,
            started_at=utc_now(),
            outcome="pending",
        )
        db.add(run)
        db.flush()

        # A newer run of a superseding kind makes an older pending batch
        # redundant. Drafted messages addressed to specific records never
        # supersede, so this list is deliberately short.
        if task.kind in SUPERSEDING_KINDS:
            superseded = (
                db.query(ScheduledTaskRun)
                .filter(
                    ScheduledTaskRun.owner_id == task.owner_id,
                    ScheduledTaskRun.task_id == task.id,
                    ScheduledTaskRun.id != run.id,
                    ScheduledTaskRun.outcome == "pending",
                )
                .all()
            )
            for older in superseded:
                older.outcome = "expired"
                older.expired_at = utc_now()
                older.expiry_reason = "superseded"

        try:
            handler_for(task.kind)(db, task, run)
        except Exception as exc:
            db.rollback()
            return _record_scheduled_failure(db, task_id, exc)

        task.last_run_at = run.started_at
        task.last_error = None
        task.consecutive_failures = 0
        db.commit()
        return {"status": "ok", "run_id": run.id, "outcome": run.outcome, "items": run.item_count}
    finally:
        db.close()


def _record_scheduled_failure(db, task_id: int, exc: Exception) -> dict[str, Any]:
    """Record the failure on both the task and a run, then notify."""
    from app.models import ScheduledTask, ScheduledTaskRun, utc_now
    from app.services import proactive_notification_service

    logger.exception("scheduled_task_failed task_id=%s", task_id)
    task = db.get(ScheduledTask, task_id)
    if task is None:
        return {"status": "failed", "reason": "task_not_found"}
    message = f"{type(exc).__name__}: {exc}"[:500]
    task.last_error = message
    task.consecutive_failures = (task.consecutive_failures or 0) + 1
    task.last_run_at = utc_now()
    db.add(
        ScheduledTaskRun(
            owner_id=task.owner_id,
            task_id=task.id,
            started_at=utc_now(),
            finished_at=utc_now(),
            outcome="failed",
            error=message,
        )
    )
    db.commit()
    proactive_notification_service.notify_scheduled(
        db,
        title=f"{task.title}: run failed",
        body=message,
        kind="failure",
    )
    return {"status": "failed", "error": message}


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
        try:
            from app.services.proactive_notification_service import generate_reply_notifications

            generate_reply_notifications(db)
        except Exception:
            logger.exception("proactive_notification_generation_failed run_key=%r", run_key)
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


def run_retry_selected_messages_job(*, run_key: str, external_message_ids: list[str]) -> dict[str, Any]:
    from app import main

    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            total_items=len(external_message_ids),
            status="running",
            detail=f"Retry worker started for {len(external_message_ids)} selected email(s).",
        )
        items = main.get_candidates_by_message_ids(external_message_ids)
        response = main._run_automation(None, db, run_key_override=run_key, items_override=items)
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=len(external_message_ids),
            total_items=len(external_message_ids),
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
