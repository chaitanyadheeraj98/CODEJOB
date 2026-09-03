"""A digest of pending work.

Not to be confused with `_build_telegram_digest` in main.py, which formats one
automation run's counters. Different thing, same word: this one summarises what
is *waiting for the user*, across every producer.

Renders through the existing metric_cards and ranked_list kinds - no new
component - and carries a provenance block like every other analysis payload,
because a count the user cannot trace is a number with no author.
"""

from __future__ import annotations

import json

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.mcp_server.tools import provenance
from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    Application,
    ApplicationSuggestion,
    EmailReplyMessage,
    RecruiterEmail,
    ScheduledTask,
    ScheduledTaskRun,
    utc_now,
)
from app.services import proactive_notification_service


def collect_sections(
    db: Session, *, owner_id: str, now=None, exclude_run_id: int | None = None
) -> list[dict[str, object]]:
    """The five pending-work counts, each with the query that produced it.

    `exclude_run_id` is not optional in practice: the job creates the run row as
    `pending` before dispatching, so a digest counting pending runs counts
    itself and always reports at least one item waiting. That would be a
    permanent off-by-one in the number the user reads every morning.
    """
    moment = now or utc_now()

    needs_review = (
        db.query(func.count(RecruiterEmail.id))
        .filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.state == "needs_review")
        .scalar()
        or 0
    )
    replies = (
        db.query(func.count(EmailReplyMessage.id))
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.direction == "inbound",
            EmailReplyMessage.notified_at.is_(None),
        )
        .scalar()
        or 0
    )
    due_actions = (
        db.query(func.count(Application.id))
        .filter(
            Application.owner_id == owner_id,
            Application.deleted_at.is_(None),
            Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
            Application.next_action_at.isnot(None),
            Application.next_action_at <= moment,
        )
        .scalar()
        or 0
    )
    suggestions = (
        db.query(func.count(ApplicationSuggestion.id))
        .filter(
            ApplicationSuggestion.owner_id == owner_id,
            ApplicationSuggestion.status == "pending",
        )
        .scalar()
        or 0
    )
    pending_runs_query = db.query(func.count(ScheduledTaskRun.id)).filter(
        ScheduledTaskRun.owner_id == owner_id, ScheduledTaskRun.outcome == "pending"
    )
    if exclude_run_id is not None:
        pending_runs_query = pending_runs_query.filter(ScheduledTaskRun.id != exclude_run_id)
    pending_runs = pending_runs_query.scalar() or 0

    return [
        {"label": "Candidates awaiting review", "value": int(needs_review), "page": "needs_review"},
        {"label": "Replies awaiting action", "value": int(replies), "page": "inbox"},
        {"label": "Applications with a due action", "value": int(due_actions), "page": "application_tracking"},
        {"label": "Pending suggestions", "value": int(suggestions), "page": "application_tracking"},
        {"label": "Prepared runs awaiting approval", "value": int(pending_runs), "page": "scheduled_review"},
    ]


def build_payload(
    db: Session, *, owner_id: str, now=None, exclude_run_id: int | None = None
) -> dict[str, object]:
    moment = now or utc_now()
    sections = collect_sections(
        db, owner_id=owner_id, now=moment, exclude_run_id=exclude_run_id
    )
    total = sum(int(section["value"]) for section in sections)
    return {
        "kind": "digest",
        "total": total,
        "cards": [
            {
                "label": section["label"],
                "value": section["value"],
                "unit": "",
                "delta": None,
                "drill_to": {"page": section["page"], "tab": None, "filters": {}},
            }
            for section in sections
        ],
        "provenance": provenance.block(
            metric="Pending work",
            source="scheduling/handlers/digest",
            row_count=total,
            filters={"owner": "current"},
            assumptions=[
                "Counts live records only: deleted applications and closed statuses are excluded.",
                "A due action means next_action_at is in the past, not that it is overdue by any margin.",
                "Every count is live at the moment the digest ran, not over the digest's period.",
            ],
        ),
    }


def run_digest(db: Session, task: ScheduledTask, run: ScheduledTaskRun) -> None:
    payload = build_payload(db, owner_id=task.owner_id, exclude_run_id=run.id)
    total = int(payload["total"])

    if total:
        body = "\n".join(
            f"- {card['label']}: {card['value']}" for card in payload["cards"] if card["value"]
        )
    else:
        # An explicit "nothing pending" rather than empty cards: a digest that
        # renders blank is indistinguishable from a digest that failed.
        body = "Nothing is pending. No candidates, replies, actions, suggestions or runs are waiting."

    proactive_notification_service.notify_scheduled(
        db, title=task.title, body=body, kind="digest"
    )

    run.outcome = "notified"
    run.finished_at = utc_now()
    run.item_count = total
    run.expires_at = None
    run.prepared_json = json.dumps(payload, separators=(",", ":"), default=str)
