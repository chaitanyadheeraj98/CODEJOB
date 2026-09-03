"""Condition monitors: evaluate a whitelisted predicate and report what fired.

A monitor notifies; it never prepares consequential work. That is `workflow`'s
job, and keeping them separate is what makes "a monitor cannot send anything"
true by construction rather than by review.
"""

from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import ScheduledTask, ScheduledTaskRun, utc_now
from app.services import proactive_notification_service
from app.services.scheduling.conditions import UnknownPredicate, evaluate


def run_monitor(db: Session, task: ScheduledTask, run: ScheduledTaskRun) -> None:
    try:
        condition = json.loads(task.condition_json or "{}")
    except (TypeError, ValueError) as exc:
        raise UnknownPredicate("This monitor's condition is unreadable") from exc
    if not isinstance(condition, dict):
        raise UnknownPredicate("This monitor's condition is unreadable")

    result = evaluate(db, owner_id=task.owner_id, condition=condition)

    if result.fired:
        subjects = len(result.subject_ids)
        body = "\n".join(
            f"- {entry.normalized_to}: {entry.left_value} (threshold {entry.right_value})"
            for entry in result.evidence
        )
        headline = f"{subjects} record(s) matched." if subjects else "The condition is met."
        proactive_notification_service.notify_scheduled(
            db, title=task.title, body=f"{headline}\n\n{body}".strip(), kind="monitor"
        )

    # "notified" for both outcomes. A monitor has nothing to approve either way,
    # and leaving a quiet run "pending" would mean it later "expired", which
    # reads as a failure in run history for a check that worked correctly.
    # item_count distinguishes them: 0 means the condition was not met.
    run.outcome = "notified"
    run.finished_at = utc_now()
    run.item_count = len(result.subject_ids)
    run.expires_at = None
    run.prepared_json = json.dumps(
        {
            "kind": "monitor",
            "fired": result.fired,
            "checked": result.checked,
            "truncated": result.truncated,
            "subject_ids": result.subject_ids[:100],
            "evidence": [entry.as_dict() for entry in result.evidence],
        },
        separators=(",", ":"),
    )
