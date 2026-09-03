"""Draft-and-hold: prepare consequential work, then stop.

This is the only handler that produces items requiring approval, and it is the
reason the run job's write surface is asserted by test. It prepares into
`prepared_json` and changes nothing else. Approval - a user click, never a tool
- executes each item through the v2 endpoint that already validates it.

`payload` is shaped for that existing endpoint on purpose. Nothing new
validates a record change; the approval path is a call to a route that has been
validating this shape since v2.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import Application, ScheduledTask, ScheduledTaskRun, utc_now
from app.services import proactive_notification_service
from app.services.scheduling.conditions import UnknownPredicate, evaluate

# The one action v4 prepares. It maps to PATCH /applications/{id}, whose request
# model is ApplicationPatchRequest - a route shipped and validated since v2.
ACTION_SET_NEXT_ACTION = "propose_record_update"

DEFAULT_RETENTION_HOURS = 168
MAX_PREPARED_ITEMS = 50


def _condition(task: ScheduledTask) -> dict:
    try:
        parsed = json.loads(task.condition_json or "{}")
    except (TypeError, ValueError) as exc:
        raise UnknownPredicate("This workflow's condition is unreadable") from exc
    if not isinstance(parsed, dict):
        raise UnknownPredicate("This workflow's condition is unreadable")
    return parsed


def _action(task: ScheduledTask) -> dict:
    try:
        parsed = json.loads(task.action_json or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_workflow(db: Session, task: ScheduledTask, run: ScheduledTaskRun) -> None:
    condition = _condition(task)
    action = _action(task)
    result = evaluate(db, owner_id=task.owner_id, condition=condition)

    next_action_type = str(action.get("next_action_type") or "Follow up with recruiter")[:80]
    items: list[dict[str, object]] = []

    for subject_id in result.subject_ids[:MAX_PREPARED_ITEMS]:
        application = db.get(Application, int(subject_id))
        if application is None or application.owner_id != task.owner_id:
            continue
        label = (
            f"{application.job_title_snapshot or 'Application'} "
            f"at {application.end_client_snapshot or 'unknown client'}"
        )
        items.append(
            {
                "item_id": str(uuid.uuid4()),
                "action": ACTION_SET_NEXT_ACTION,
                "record_kind": "application",
                "record_id": int(application.id),
                "subject_type": "application",
                "subject_id": str(application.id),
                "summary": f"Set next action on {label} - {next_action_type}",
                # Exactly ApplicationPatchRequest's shape.
                "payload": {
                    "next_action_type": next_action_type,
                    "next_action_at": utc_now().isoformat(),
                },
                "editable_fields": ["next_action_type", "next_action_at"],
            }
        )

    retention = int(task.retention_hours or DEFAULT_RETENTION_HOURS)
    run.item_count = len(items)
    run.prepared_json = json.dumps(
        {
            "kind": "workflow",
            "items": items,
            "checked": result.checked,
            "truncated": result.truncated or len(result.subject_ids) > MAX_PREPARED_ITEMS,
            "evidence": [entry.as_dict() for entry in result.evidence],
        },
        separators=(",", ":"),
    )

    if not items:
        # Nothing to approve, so nothing to hold. Leaving an empty batch pending
        # would expire later and read as a failure in run history.
        run.outcome = "notified"
        run.expires_at = None
        run.finished_at = utc_now()
        return

    run.outcome = "pending"
    run.expires_at = run.started_at + timedelta(hours=retention)
    run.finished_at = utc_now()
    proactive_notification_service.notify_scheduled(
        db,
        title=f"{task.title}: {len(items)} item(s) ready for review",
        body="Nothing has been changed. Open Scheduled review to approve, edit or discard.",
        kind="workflow",
    )
