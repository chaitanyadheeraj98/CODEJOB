"""One-time and recurring reminders: notify and complete.

The smallest useful slice, and the one with zero action risk - a reminder that
only notifies cannot send anything or change any record.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import ScheduledTask, ScheduledTaskRun, utc_now
from app.services import proactive_notification_service


def run_reminder(db: Session, task: ScheduledTask, run: ScheduledTaskRun) -> None:
    """Notify and finish. Prepares nothing, so there is nothing to approve."""
    try:
        action = json.loads(task.action_json or "{}")
    except (TypeError, ValueError):
        action = {}
    note = str(action.get("note") or "").strip()

    body = note or "This reminder is due."
    proactive_notification_service.notify_scheduled(
        db, title=task.title, body=body, kind="reminder"
    )

    # "notified", not "pending". A reminder with no prepared work must not sit
    # pending and then "expire", which would read as a failure in run history.
    run.outcome = "notified"
    run.finished_at = utc_now()
    run.item_count = 0
    run.expires_at = None
    run.prepared_json = json.dumps({"kind": "reminder", "note": body}, separators=(",", ":"))
