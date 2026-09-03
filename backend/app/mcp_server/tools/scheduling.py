"""Scheduled tasks: one read tool and one proposal tool.

Two tools for the whole family, following v2's budget rule: one tool per family
with an enum argument, not one tool per operation. Four separate create/pause/
edit/delete tools would be the mistake that discipline exists to prevent.

`propose_scheduled_task` **never writes**. It returns a preview payload; the
user's click on the rendered card calls the route. That is identical to every v2
proposal tool, and it is why v4 introduces no model-callable path that approves,
sends, or changes anything.

The `when` phrase is parsed **here, on the server**, and the payload carries the
system's interpretation in plain language. A card that echoed the user's words
back would confirm nothing: the point is for the user to check what the system
understood, not what they typed.
"""

from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.mcp_server.tools import provenance
from app.models import (
    SCHEDULED_TASK_KINDS,
    SCHEDULED_TASK_STATUS_VALUES,
    ScheduledTask,
    ScheduledTaskRun,
    utc_now,
)
from app.services.scheduling.conditions import describe_predicates
from app.services.scheduling.schedule import (
    SUPPORTED_PHRASINGS,
    InvalidSchedule,
    ScheduleSpec,
    describe,
    granularity_note,
    next_run_after,
    parse_when,
)
from app.services.scheduling.timezone import user_zone

MAX_TASKS = 25
OPERATIONS = ("create", "pause", "resume", "edit", "delete")

# What each kind is permitted to do, stated from the kind rather than from a
# label someone typed. The management view renders the same strings.
PERMITTED_ACTIONS = {
    "reminder": "Notifies you. Changes nothing.",
    "digest": "Summarises pending work and notifies you. Changes nothing.",
    "monitor": "Checks a condition and notifies you when it is met. Changes nothing.",
    "workflow": "Prepares changes for your review. Nothing is applied without your approval.",
    "checklist": "Holds a list you tick off. Changes nothing on its own.",
}


def _spec(task: ScheduledTask) -> ScheduleSpec:
    return ScheduleSpec(
        schedule_kind=task.schedule_kind,
        cron_expression=task.cron_expression or "",
        run_at=task.run_at,
        timezone=task.timezone or "UTC",
    )


def _task_payload(task: ScheduledTask, run_count: int = 0) -> dict[str, object]:
    return {
        "id": int(task.id),
        "title": task.title,
        "kind": task.kind,
        "status": task.status,
        "trigger": describe(_spec(task)),
        "next_run_at": task.next_run_at.isoformat() if task.next_run_at else None,
        "last_run_at": task.last_run_at.isoformat() if task.last_run_at else None,
        "permitted_actions": PERMITTED_ACTIONS.get(task.kind, "Unknown"),
        "timezone": task.timezone or "UTC",
        "last_error": task.last_error or "",
        "run_count": run_count,
    }


def list_scheduled_tasks(status: str = "active", limit: int = 20) -> dict[str, object]:
    """Scheduled tasks with their trigger, next run, and what each may do.

    status is one of active, paused, suspended, deleted, or "all". Use this for
    "what have you got scheduled", "is anything running", or before proposing a
    change to a task the user described in words rather than by id.

    Every task the user owns appears here. Nothing is hidden by kind or by flag:
    a scheduled task the user cannot see is one they cannot stop.
    """
    normalized = (status or "active").strip().lower()
    if normalized != "all" and normalized not in SCHEDULED_TASK_STATUS_VALUES:
        return {
            "error": f"Unknown status '{status}'.",
            "statuses": [*SCHEDULED_TASK_STATUS_VALUES, "all"],
        }
    capped = max(1, min(int(limit or 20), MAX_TASKS))

    db = SessionLocal()
    try:
        query = db.query(ScheduledTask).filter(ScheduledTask.owner_id == settings.owner_id)
        if normalized == "all":
            query = query.filter(ScheduledTask.status != "deleted")
        else:
            query = query.filter(ScheduledTask.status == normalized)
        rows = query.order_by(ScheduledTask.next_run_at.asc(), ScheduledTask.id.asc()).all()
        total = len(rows)
        shown = rows[:capped]
        counts = {
            row.id: db.query(ScheduledTaskRun).filter(ScheduledTaskRun.task_id == row.id).count()
            for row in shown
        }
        payload = [_task_payload(row, counts.get(row.id, 0)) for row in shown]
    finally:
        db.close()

    return {
        "action": "render_scheduled_tasks",
        "tasks": payload,
        "truncated": total > len(payload),
        "granularity_note": granularity_note(),
        "provenance": provenance.block(
            metric="Scheduled tasks",
            source="list_scheduled_tasks",
            row_count=total,
            filters={"status": normalized},
            assumptions=[
                "Deleted tasks are excluded unless you ask for them by status.",
                "Next run is shown in the zone each task was created in, which is named beside it.",
            ],
        ),
    }


def propose_scheduled_task(
    operation: str,
    title: str = "",
    kind: str = "reminder",
    when: str = "",
    note: str = "",
    subject_type: str = "",
    subject_id: str = "",
    task_id: int = 0,
) -> dict[str, object]:
    """Prepare a scheduled-task change for the user to confirm. Writes nothing.

    operation is one of create, pause, resume, edit, delete.
    kind is one of reminder, digest, monitor, workflow, checklist.
    `when` is the user's own phrasing - "every weekday at 9am", "in 2 hours".
    The server parses it and the card shows what it understood.

    Returns a preview the user confirms with a click. If `when` cannot be read,
    this returns status "unparseable" with the phrasings that do work and
    creates nothing: a schedule the system guessed at is worse than no schedule.
    """
    action = (operation or "").strip().lower()
    if action not in OPERATIONS:
        return {"error": f"Unknown operation '{operation}'.", "operations": list(OPERATIONS)}

    if action in ("pause", "resume", "delete"):
        return _lifecycle_preview(action, int(task_id or 0))

    task_kind = (kind or "reminder").strip().lower()
    if task_kind not in SCHEDULED_TASK_KINDS:
        return {"error": f"Unknown kind '{kind}'.", "kinds": list(SCHEDULED_TASK_KINDS)}

    label = (title or "").strip()
    if not label:
        return {"status": "missing_fields", "missing": ["title"]}

    db = SessionLocal()
    try:
        zone = str(user_zone(db, owner_id=settings.owner_id))
        existing = None
        if action == "edit":
            existing = (
                db.query(ScheduledTask)
                .filter(
                    ScheduledTask.owner_id == settings.owner_id,
                    ScheduledTask.id == int(task_id or 0),
                )
                .first()
            )
            if existing is None:
                return {"status": "not_found", "detail": "No such scheduled task."}
    finally:
        db.close()

    # A checklist has no schedule, so it needs no phrase.
    if task_kind == "checklist":
        spec = ScheduleSpec(schedule_kind="none", timezone=zone)
    elif task_kind == "monitor":
        spec = ScheduleSpec(schedule_kind="condition", timezone=zone)
    else:
        try:
            spec = parse_when(when, timezone=zone)
        except InvalidSchedule as exc:
            return {
                "status": "unparseable",
                "detail": str(exc),
                "supported_phrasings": list(SUPPORTED_PHRASINGS),
                "predicates": describe_predicates() if task_kind == "monitor" else [],
            }

    next_run = next_run_after(spec, utc_now())

    return {
        "action": "propose_scheduled_task",
        "status": "preview",
        "operation": action,
        "task_id": int(task_id or 0),
        "title": label,
        "kind": task_kind,
        "note": (note or "").strip(),
        "schedule_kind": spec.schedule_kind,
        "cron_expression": spec.cron_expression,
        "run_at": spec.run_at.isoformat() if spec.run_at else None,
        "timezone": spec.timezone,
        "subject_type": (subject_type or "").strip(),
        "subject_id": (subject_id or "").strip(),
        # The four things §7.2 requires on the card, plus the caveat the user
        # would otherwise discover by being late.
        "trigger": describe(spec),
        "first_run": next_run.isoformat() if next_run else None,
        "permitted_actions": PERMITTED_ACTIONS[task_kind],
        "granularity_note": granularity_note(),
        "reversible": True,
        "reversible_detail": "Creating a task changes no records. You can pause or delete it at any time.",
    }


def _lifecycle_preview(action: str, task_id: int) -> dict[str, object]:
    if task_id <= 0:
        return {"status": "missing_fields", "missing": ["task_id"]}
    db = SessionLocal()
    try:
        task = (
            db.query(ScheduledTask)
            .filter(ScheduledTask.owner_id == settings.owner_id, ScheduledTask.id == task_id)
            .first()
        )
        if task is None:
            return {"status": "not_found", "detail": "No such scheduled task."}
        payload = _task_payload(task)
    finally:
        db.close()

    labels = {
        "pause": "Pause this task. It stops running until you resume it.",
        "resume": "Resume this task. Its next run is recomputed from now.",
        "delete": "Delete this task. Its run history stays readable.",
    }
    return {
        "action": "propose_scheduled_task",
        "status": "preview",
        "operation": action,
        "task_id": task_id,
        "title": payload["title"],
        "kind": payload["kind"],
        "trigger": payload["trigger"],
        "first_run": payload["next_run_at"],
        "permitted_actions": payload["permitted_actions"],
        "detail": labels[action],
        "reversible": action != "delete",
        "reversible_detail": (
            "Deleted tasks stop running; their history is kept."
            if action == "delete"
            else "You can change this back at any time."
        ),
    }
