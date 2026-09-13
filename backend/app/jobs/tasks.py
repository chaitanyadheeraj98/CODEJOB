from __future__ import annotations

import asyncio
import logging
import json
from typing import Any
from uuid import uuid4

from app.db import SessionLocal
from app.tenancy import owner_scoped
from app.jobs.progress import update_job_progress
from app.schemas import AutomationRunRequest

logger = logging.getLogger(__name__)


@owner_scoped
def run_telegram_chat_turn(*, chat_id: int, message_id: int, text: str) -> dict[str, Any]:
    from fastapi import HTTPException

    from app import tenancy
    from app.config import settings
    from app.services.chat_service import ChatService
    from app.services.telegram_chat_service import current_session
    from app.services.telegram_format import email_proposal, format_answer, unicode_chart
    from app.telegram_bot import TelegramTransport

    correlation_id = uuid4().hex
    response = ""
    proposal_replies: list[tuple[str, list[list[dict[str, str]]] | None]] = []
    chart_replies: list[str] = []
    status = "ok"
    db = SessionLocal()
    try:
        session = current_session(db, tenancy.owner_id(), chat_id)
        result = asyncio.run(ChatService().complete_message(db, session.id, text, model=None))
        if result.failure_code in {"budget_exhausted", "tool_budget_exhausted"}:
            response = "That took too long and I stopped. Try narrowing the question."
            status = "timeout"
        else:
            response = format_answer(result.assistant_text or "Something went wrong on my side.")
            status = "ok" if result.assistant_text else "failed"
            proposal_replies = [
                rendered
                for row in result.tool_rows
                if row.tool_name == "propose_send_email"
                for rendered in [email_proposal(row.content, row.id)]
                if rendered is not None
            ]
            chart_replies = [
                rendered
                for row in result.tool_rows
                if row.tool_name == "get_chart"
                for rendered in [unicode_chart(row.content)]
                if rendered is not None
            ]
    except HTTPException as exc:
        if exc.status_code == 503:
            response = "I'm at capacity right now — send that again in a moment."
            status = "capacity"
        elif exc.status_code == 422:
            response = "That message is too long for me to read — try splitting it."
            status = "invalid"
        else:
            response = f"Something went wrong on my side. Reference: <code>{correlation_id}</code>"
            status = "failed"
    except (TimeoutError, asyncio.TimeoutError):
        response = "That took too long and I stopped. Try narrowing the question."
        status = "timeout"
    except Exception as exc:
        logger.warning(
            "telegram_chat_turn_failed correlation_id=%s chat_id=%s error_type=%s",
            correlation_id,
            chat_id,
            type(exc).__name__,
        )
        response = f"Something went wrong on my side. Reference: <code>{correlation_id}</code>"
        status = "failed"
    finally:
        db.close()

    try:
        transport = TelegramTransport(settings.telegram_bot_token)
        transport.edit_message(chat_id, message_id, response)
        for proposal_text, keyboard in proposal_replies:
            transport.send_message(chat_id, proposal_text, inline_keyboard=keyboard)
        for chart_text in chart_replies:
            transport.send_message(chat_id, chart_text)
    except Exception as exc:
        logger.warning(
            "telegram_chat_reply_failed correlation_id=%s chat_id=%s error_type=%s",
            correlation_id,
            chat_id,
            type(exc).__name__,
        )
        return {"status": "delivery_failed", "correlation_id": correlation_id}
    return {"status": status, "correlation_id": correlation_id}


@owner_scoped
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


@owner_scoped
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


@owner_scoped
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


@owner_scoped
def run_nvoids_sync_job(
    *, run_key: str, max_items: int, criteria: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The scheduled sync, and - when `criteria` is given - one search for a
    named company. Same work either way; only the query differs."""
    from app import main
    from app.services import nvoids_search_job

    search = (
        nvoids_search_job.SearchCriteria(
            end_client=str(criteria.get("end_client") or ""),
            job_role=str(criteria.get("job_role") or ""),
            search_location=str(criteria.get("search_location") or ""),
            query_mode=str(criteria.get("query_mode") or "composed"),
            batch_limit=int(criteria.get("batch_limit") or max_items),
        )
        if criteria
        else None
    )

    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            total_items=max_items,
            status="running",
            detail=(
                f"Searching nvoids for {search.end_client}."
                if search and search.end_client
                else "Nvoids sync worker started."
            ),
        )
        result = main._run_nvoids_sync(
            db,
            max_items=max_items,
            run_key_override=run_key,
            criteria_override=search,
            progress_callback=lambda processed, total: update_job_progress(
                db,
                run_key=run_key,
                processed_items=processed,
                total_items=total,
                status="running",
            ),
        )
        if search is not None:
            # The §16 outcome in words, written where the completion is read
            # from. `fetched` is what the crawl returned and `created` what
            # survived dedupe and the phone gate - reporting only the first
            # overstates the result about fourfold.
            outcome = nvoids_search_job.summarize_outcome(
                found=result.fetched_count, imported=result.created_count
            )
            detail = f"{nvoids_search_job.DETAIL_PREFIX}{outcome['outcome']} | {outcome['message']}"
        else:
            detail = (
                "nvoids sync complete: "
                f"fetched={result.fetched_count} created={result.created_count} "
                f"deduped={result.deduped_count} skipped_location={result.skipped_location_count} "
                f"failed={result.failed_count}"
            )
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=max(result.fetched_count, result.created_count + result.deduped_count),
            status="ok",
            detail=detail,
            complete=True,
        )
        return {"run_key": run_key, "status": row.status}
    except Exception as exc:
        db.rollback()
        _mark_failed(run_key, exc)
        raise
    finally:
        db.close()


@owner_scoped
def run_manual_intake_job(*, run_key: str, text: str) -> dict[str, Any]:
    """Ingest one pasted requirement.

    One item, not a sweep, so `total_items` is 1 and the user is very likely
    watching the status line while this runs.
    """
    from app import main

    db = SessionLocal()
    try:
        update_job_progress(
            db,
            run_key=run_key,
            total_items=1,
            status="running",
            detail="Reading the pasted requirement.",
        )
        result = main._run_manual_intake(db, text=text)
        row = update_job_progress(
            db,
            run_key=run_key,
            processed_items=1,
            total_items=1,
            status="ok",
            detail=result.detail,
            complete=True,
        )
        return {
            "run_key": run_key,
            "status": row.status,
            "candidate_email_id": result.email_id,
            "state": result.state,
        }
    except Exception as exc:
        db.rollback()
        _mark_failed(run_key, exc)
        raise
    finally:
        db.close()


@owner_scoped
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


@owner_scoped
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


@owner_scoped
def run_gmail_history_job(*, notification_history_id: str = "") -> dict[str, Any]:
    """Drain one mailbox's Gmail history after a push notification.

    Thin on purpose. Everything that decides what changed lives in
    `gmail_pubsub_service`; what belongs to the job layer is the owner scope,
    the retry policy, and returning something an operator can read in the RQ
    dashboard.

    `owner_id` is supplied by the decorator from the enqueue call, never from
    the Pub/Sub payload - the subscriber resolves a mailbox address to an owner
    against the credential table and enqueues that. A notification cannot name
    the tenant it writes to.

    No `job_id` derived from the notification. Several notifications can
    legitimately coalesce into one history range, and deduplicating on the
    number would drop drains that were needed. The per-owner history lock, the
    committed cursor and the `UNIQUE(owner_id, external_message_id)` constraint
    already make a repeat harmless.
    """
    from app.services import gmail_pubsub_service

    outcome = gmail_pubsub_service.process_history(
        _owner_id_in_scope(), notification_history_id
    )
    return {
        "status": "ok" if not outcome.reason else outcome.reason,
        "processed": outcome.processed,
        "captured": outcome.captured,
        "recovered": outcome.recovered,
    }


def _owner_id_in_scope() -> str:
    from app import tenancy

    return tenancy.owner_id()
