"""Scheduled-task CRUD and run approval.

Keeps `main.py` thin, the way v3's services do. Two things here carry real
weight:

**The completeness invariant.** `list_tasks` returns every non-deleted task, and
a test asserts it. No kind, no `schedule_kind`, and no internal flag may hide a
task from the management view - a scheduled task the user cannot see is one they
cannot stop.

**The approval endpoint's double refusal.** It checks `outcome != "pending"`
*and* compares `expires_at` against the clock itself. The second check exists
because this codebase ships two `expires_at` columns that nothing ever compares;
trusting a sweep to have run is precisely how that happens.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    SCHEDULED_TASK_KINDS,
    Application,
    ScheduledTask,
    ScheduledTaskItem,
    ScheduledTaskRun,
    utc_now,
)
from app.services import application_service
from app.services.scheduling.conditions import describe_predicates
from app.services.scheduling.pending_work import prepared_items
from app.services.scheduling.schedule import (
    InvalidSchedule,
    ScheduleSpec,
    describe,
    next_run_after,
    parse_when,
)
from app.services.scheduling.timezone import user_zone

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_HOURS = 168
# Digests and monitors go stale faster than drafts: a draft is worth reviewing
# late, yesterday's digest is not.
SHORT_RETENTION_KINDS = {"digest", "monitor"}
SHORT_RETENTION_HOURS = 48


class TaskNotFound(LookupError):
    """Out of scope and nonexistent, deliberately indistinguishable."""


class TaskInvalid(ValueError):
    """A request this system will not honour, with a reason the user can act on."""


@dataclass(frozen=True)
class ApprovalResult:
    run_id: int
    approved: int
    failed: int
    outcome: str
    errors: list[str]


def spec_for(task: ScheduledTask) -> ScheduleSpec:
    return ScheduleSpec(
        schedule_kind=task.schedule_kind,
        cron_expression=task.cron_expression or "",
        run_at=task.run_at,
        timezone=task.timezone or "UTC",
    )


def _default_retention(kind: str) -> int:
    return SHORT_RETENTION_HOURS if kind in SHORT_RETENTION_KINDS else DEFAULT_RETENTION_HOURS


def _resolve_schedule(kind: str, when: str, zone: str) -> ScheduleSpec:
    if kind == "checklist":
        return ScheduleSpec(schedule_kind="none", timezone=zone)
    if kind == "monitor":
        return ScheduleSpec(schedule_kind="condition", timezone=zone)
    try:
        return parse_when(when, timezone=zone)
    except InvalidSchedule as exc:
        raise TaskInvalid(str(exc)) from exc


def create_task(
    db: Session,
    *,
    owner_id: str,
    title: str,
    kind: str,
    when: str = "",
    note: str = "",
    subject_type: str = "",
    subject_id: str = "",
    condition: dict | None = None,
    action: dict | None = None,
    items: list[str] | None = None,
    retention_hours: int | None = None,
) -> ScheduledTask:
    task_kind = (kind or "reminder").strip().lower()
    if task_kind not in SCHEDULED_TASK_KINDS:
        raise TaskInvalid(f"Unknown kind '{kind}'. Kinds: {', '.join(SCHEDULED_TASK_KINDS)}")

    zone = str(user_zone(db, owner_id=owner_id))
    spec = _resolve_schedule(task_kind, when, zone)

    if task_kind in ("monitor", "workflow"):
        if not isinstance(condition, dict) or not condition:
            raise TaskInvalid(
                "This kind needs a condition. Supported predicates: "
                + "; ".join(entry["predicate"] for entry in describe_predicates())
            )

    payload_action = dict(action or {})
    if note:
        payload_action.setdefault("note", note)

    now = utc_now()
    task = ScheduledTask(
        owner_id=owner_id,
        title=title.strip(),
        kind=task_kind,
        schedule_kind=spec.schedule_kind,
        cron_expression=spec.cron_expression,
        timezone=spec.timezone,
        run_at=spec.run_at,
        condition_json=json.dumps(condition, separators=(",", ":")) if condition else None,
        action_json=json.dumps(payload_action, separators=(",", ":")),
        subject_type=(subject_type or "").strip(),
        subject_id=(subject_id or "").strip(),
        status="active",
        next_run_at=next_run_after(spec, now),
        retention_hours=int(retention_hours or _default_retention(task_kind)),
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()

    for position, text in enumerate(items or []):
        cleaned = (text or "").strip()
        if cleaned:
            db.add(
                ScheduledTaskItem(
                    owner_id=owner_id,
                    task_id=task.id,
                    position=position,
                    text=cleaned[:500],
                    created_at=now,
                )
            )
    db.commit()
    db.refresh(task)
    return task


def list_tasks(db: Session, *, owner_id: str, status: str = "all") -> list[ScheduledTask]:
    """Every task the user owns. The completeness invariant lives here."""
    query = db.query(ScheduledTask).filter(ScheduledTask.owner_id == owner_id)
    normalized = (status or "all").strip().lower()
    if normalized == "all":
        query = query.filter(ScheduledTask.status != "deleted")
    else:
        query = query.filter(ScheduledTask.status == normalized)
    return query.order_by(ScheduledTask.next_run_at.asc(), ScheduledTask.id.asc()).all()


def get_task(db: Session, *, owner_id: str, task_id: int) -> ScheduledTask:
    task = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.owner_id == owner_id, ScheduledTask.id == task_id)
        .first()
    )
    if task is None or task.status == "deleted":
        raise TaskNotFound("Scheduled task not found")
    return task


def patch_task(
    db: Session,
    *,
    owner_id: str,
    task_id: int,
    operation: str = "edit",
    title: str | None = None,
    when: str | None = None,
    note: str | None = None,
    condition: dict | None = None,
    action: dict | None = None,
    retention_hours: int | None = None,
) -> ScheduledTask:
    task = get_task(db, owner_id=owner_id, task_id=task_id)
    move = (operation or "edit").strip().lower()

    if move == "pause":
        task.status = "paused"
        # Cleared rather than kept: a stale next_run_at on resume would fire
        # immediately for every tick missed while paused.
        task.next_run_at = None
    elif move == "resume":
        task.status = "active"
        task.consecutive_failures = 0
        task.last_error = None
        task.next_run_at = next_run_after(spec_for(task), utc_now())
    elif move == "edit":
        if title is not None:
            task.title = title.strip() or task.title
        if when is not None:
            spec = _resolve_schedule(task.kind, when, task.timezone or "UTC")
            task.schedule_kind = spec.schedule_kind
            task.cron_expression = spec.cron_expression
            task.run_at = spec.run_at
            task.next_run_at = next_run_after(spec, utc_now())
        if condition is not None:
            task.condition_json = json.dumps(condition, separators=(",", ":"))
        if action is not None or note is not None:
            payload = dict(action or {})
            if note is not None:
                payload["note"] = note
            task.action_json = json.dumps(payload, separators=(",", ":"))
        if retention_hours is not None:
            task.retention_hours = int(retention_hours)
    else:
        raise TaskInvalid(f"Unknown operation '{operation}'. Use pause, resume or edit.")

    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return task


def delete_task(db: Session, *, owner_id: str, task_id: int) -> ScheduledTask:
    """Soft delete. Run history stays readable; the task stops running."""
    task = get_task(db, owner_id=owner_id, task_id=task_id)
    task.status = "deleted"
    task.next_run_at = None
    task.updated_at = utc_now()
    db.commit()
    db.refresh(task)
    return task


def task_runs(db: Session, *, owner_id: str, task_id: int, limit: int = 50) -> list[ScheduledTaskRun]:
    get_task(db, owner_id=owner_id, task_id=task_id)
    return (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.owner_id == owner_id, ScheduledTaskRun.task_id == task_id)
        .order_by(ScheduledTaskRun.started_at.desc(), ScheduledTaskRun.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )


def get_run(db: Session, *, owner_id: str, run_id: int) -> ScheduledTaskRun:
    run = (
        db.query(ScheduledTaskRun)
        .filter(ScheduledTaskRun.owner_id == owner_id, ScheduledTaskRun.id == run_id)
        .first()
    )
    if run is None:
        raise TaskNotFound("Run not found")
    return run


def _assert_approvable(run: ScheduledTaskRun, now: datetime) -> None:
    if run.outcome != "pending":
        raise TaskInvalid(f"This run is already {run.outcome} and cannot be approved.")
    # The second, independent check. F7: this codebase ships expires_at columns
    # that nothing compares, so the endpoint checks the clock itself rather than
    # trusting a sweep to have run. A stale open tab must not be able to approve
    # work that expired an hour ago.
    if run.expires_at is not None and run.expires_at <= now:
        raise TaskInvalid("This run expired before it was approved.")


def approve_run(
    db: Session,
    *,
    owner_id: str,
    run_id: int,
    item_ids: list[str] | None = None,
    edits: dict[str, dict] | None = None,
) -> ApprovalResult:
    now = utc_now()
    run = get_run(db, owner_id=owner_id, run_id=run_id)
    _assert_approvable(run, now)

    items = prepared_items(run)
    known = {str(item.get("item_id")) for item in items}
    requested = [str(value) for value in (item_ids or [])] or sorted(known)

    unknown = [value for value in requested if value not in known]
    if unknown:
        raise TaskInvalid(f"These items are not part of this run: {', '.join(unknown)}")

    approved = 0
    errors: list[str] = []
    by_id = {str(item.get("item_id")): item for item in items}
    for item_id in requested:
        item = by_id[item_id]
        payload = dict(item.get("payload") or {})
        payload.update((edits or {}).get(item_id, {}))
        try:
            _execute(db, owner_id=owner_id, item=item, payload=payload)
            approved += 1
        except Exception as exc:
            logger.exception("scheduled_run_item_failed run_id=%s item=%s", run_id, item_id)
            errors.append(f"{item.get('summary') or item_id}: {exc}")

    run.approved_count = approved
    run.approved_at = now
    run.finished_at = now
    if approved == len(items) and not errors:
        run.outcome = "approved"
    elif approved:
        run.outcome = "partially_approved"
    elif errors:
        # Nothing succeeded, so the batch stays reviewable rather than being
        # marked resolved. The user can fix the cause and approve again.
        run.outcome = "pending"
        run.approved_at = None
        run.finished_at = None
    else:
        run.outcome = "approved"
    db.commit()

    return ApprovalResult(
        run_id=run_id, approved=approved, failed=len(errors), outcome=run.outcome, errors=errors
    )


def _execute(db: Session, *, owner_id: str, item: dict, payload: dict) -> None:
    """Run one approved item through the path v2 already validates.

    The prepared payload is shaped for ApplicationPatchRequest, and this calls
    the same service function `PATCH /applications/{id}` calls. Nothing new
    validates a record change.
    """
    if item.get("record_kind") != "application":
        raise TaskInvalid(f"Unsupported prepared action: {item.get('action')}")
    application = (
        db.query(Application)
        .filter(
            Application.owner_id == owner_id,
            Application.id == int(item.get("record_id") or 0),
            Application.deleted_at.is_(None),
        )
        .first()
    )
    if application is None:
        raise TaskInvalid("That record no longer exists.")

    raw = payload.get("next_action_at")
    when = datetime.fromisoformat(raw) if isinstance(raw, str) and raw else None
    application_service.set_next_action(
        db,
        application,
        next_action_type=(payload.get("next_action_type") or None),
        next_action_at=when,
    )


def discard_run(db: Session, *, owner_id: str, run_id: int) -> ScheduledTaskRun:
    run = get_run(db, owner_id=owner_id, run_id=run_id)
    if run.outcome != "pending":
        raise TaskInvalid(f"This run is already {run.outcome}.")
    run.outcome = "discarded"
    run.finished_at = utc_now()
    db.commit()
    db.refresh(run)
    return run


def task_items(db: Session, *, owner_id: str, task_id: int) -> list[ScheduledTaskItem]:
    get_task(db, owner_id=owner_id, task_id=task_id)
    return (
        db.query(ScheduledTaskItem)
        .filter(ScheduledTaskItem.owner_id == owner_id, ScheduledTaskItem.task_id == task_id)
        .order_by(ScheduledTaskItem.position.asc(), ScheduledTaskItem.id.asc())
        .all()
    )


def patch_item(
    db: Session,
    *,
    owner_id: str,
    task_id: int,
    item_id: int,
    done: bool | None = None,
    text: str | None = None,
    position: int | None = None,
) -> ScheduledTaskItem:
    """Toggling a checklist item is a direct write, not a proposal.

    A checkbox mutates nothing beyond the checklist itself, so routing it
    through the confirmation path would train the user to click through
    confirmations that do not matter.
    """
    get_task(db, owner_id=owner_id, task_id=task_id)
    item = (
        db.query(ScheduledTaskItem)
        .filter(
            ScheduledTaskItem.owner_id == owner_id,
            ScheduledTaskItem.task_id == task_id,
            ScheduledTaskItem.id == item_id,
        )
        .first()
    )
    if item is None:
        raise TaskNotFound("Checklist item not found")
    if done is not None:
        item.done = bool(done)
        item.done_at = utc_now() if done else None
    if text is not None:
        item.text = text.strip()[:500]
    if position is not None:
        item.position = int(position)
    db.commit()
    db.refresh(item)
    return item


def task_payload(db: Session, task: ScheduledTask) -> dict[str, object]:
    from app.mcp_server.tools.scheduling import PERMITTED_ACTIONS

    run_count = (
        db.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == task.id).count()
    )
    return {
        "id": int(task.id),
        "title": task.title,
        "kind": task.kind,
        "status": task.status,
        "schedule_kind": task.schedule_kind,
        "cron_expression": task.cron_expression,
        "timezone": task.timezone or "UTC",
        "trigger": describe(spec_for(task)),
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "last_error": task.last_error or "",
        "consecutive_failures": int(task.consecutive_failures or 0),
        "retention_hours": int(task.retention_hours or DEFAULT_RETENTION_HOURS),
        # Rendered from the kind, server-side. Never a static label the client
        # chose: a user reading "may draft follow-up emails" must be reading
        # what the task will actually do.
        "permitted_actions": PERMITTED_ACTIONS.get(task.kind, "Unknown"),
        "subject_type": task.subject_type,
        "subject_id": task.subject_id,
        "run_count": run_count,
    }


EXPIRY_REASON_LABELS = {
    "not_reviewed": "Not reviewed before expiration",
    "superseded": "Superseded by a newer run",
}


def run_payload(run: ScheduledTaskRun) -> dict[str, object]:
    return {
        "id": int(run.id),
        "task_id": int(run.task_id),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "outcome": run.outcome,
        "item_count": int(run.item_count or 0),
        "approved_count": int(run.approved_count or 0),
        "expires_at": run.expires_at.isoformat() if run.expires_at else None,
        "expired_at": run.expired_at.isoformat() if run.expired_at else None,
        "expiry_reason": EXPIRY_REASON_LABELS.get(run.expiry_reason or "", ""),
        "error": run.error or "",
        "items": prepared_items(run),
    }
