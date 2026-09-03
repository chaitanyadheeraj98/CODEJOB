"""One review surface, whatever produced the work.

Two "pending work awaiting your approval" systems in one product is a defect the
user experiences directly: two inboxes, two approval controls, two places to
miss something. `ApplicationSuggestion` shipped first and keeps its own accept
semantics per suggestion_type; `ScheduledTaskRun` is the general mechanism. They
render through one contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    Application,
    ApplicationSuggestion,
    ScheduledTask,
    ScheduledTaskRun,
)

SOURCE_SCHEDULED_RUN = "scheduled_run"
SOURCE_APPLICATION_SUGGESTION = "application_suggestion"

# A key into the client's endpoint table, never a URL from the server. A
# malformed payload must not be able to aim a POST anywhere.
APPROVE_KEYS = {
    SOURCE_SCHEDULED_RUN: "scheduled_run_approve",
    SOURCE_APPLICATION_SUGGESTION: "application_suggestion_accept",
}


@dataclass(frozen=True)
class PendingWorkItem:
    source: str
    source_id: int
    # The task a scheduled run belongs to, so the review view can fetch its
    # prepared items. Zero for suggestions, which have no task. Distinct from
    # subject_id, which is the *record* the work is about.
    task_id: int
    title: str
    detail: str
    subject_type: str
    subject_id: str
    prepared_at: datetime | None
    expires_at: datetime | None
    item_count: int
    approve_endpoint: str

    def as_dict(self) -> dict[str, object]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "task_id": self.task_id,
            "title": self.title,
            "detail": self.detail,
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "prepared_at": self.prepared_at.isoformat() if self.prepared_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "item_count": self.item_count,
            "approve_endpoint": self.approve_endpoint,
        }


def _run_items(db: Session, owner_id: str) -> list[PendingWorkItem]:
    rows = (
        db.query(ScheduledTaskRun)
        .filter(
            ScheduledTaskRun.owner_id == owner_id,
            ScheduledTaskRun.outcome == "pending",
        )
        .order_by(ScheduledTaskRun.started_at.desc())
        .all()
    )
    items: list[PendingWorkItem] = []
    for row in rows:
        task = db.get(ScheduledTask, row.task_id)
        items.append(
            PendingWorkItem(
                source=SOURCE_SCHEDULED_RUN,
                source_id=int(row.id),
                task_id=int(row.task_id),
                title=task.title if task else f"Run {row.id}",
                detail=f"{row.item_count} prepared item(s) awaiting review",
                subject_type=task.subject_type if task else "",
                subject_id=task.subject_id if task else "",
                prepared_at=row.started_at,
                expires_at=row.expires_at,
                item_count=int(row.item_count or 0),
                approve_endpoint=APPROVE_KEYS[SOURCE_SCHEDULED_RUN],
            )
        )
    return items


def _suggestion_items(db: Session, owner_id: str) -> list[PendingWorkItem]:
    rows = (
        db.query(ApplicationSuggestion)
        .filter(
            ApplicationSuggestion.owner_id == owner_id,
            ApplicationSuggestion.status == "pending",
        )
        .order_by(ApplicationSuggestion.created_at.desc())
        .all()
    )
    items: list[PendingWorkItem] = []
    for row in rows:
        application = db.get(Application, row.application_id)
        label = (
            f"{application.job_title_snapshot} at {application.end_client_snapshot}"
            if application
            else f"Application {row.application_id}"
        )
        items.append(
            PendingWorkItem(
                source=SOURCE_APPLICATION_SUGGESTION,
                source_id=int(row.id),
                task_id=0,
                title=f"{row.suggestion_type.replace('_', ' ').capitalize()}: {label}",
                detail=row.reason or "",
                subject_type="application",
                subject_id=str(row.application_id),
                prepared_at=row.created_at,
                expires_at=row.expires_at,
                item_count=1,
                approve_endpoint=APPROVE_KEYS[SOURCE_APPLICATION_SUGGESTION],
            )
        )
    return items


def pending_work(db: Session, *, owner_id: str, limit: int = 200) -> list[PendingWorkItem]:
    """Everything awaiting the user's approval, from both producers, newest first."""
    items = _run_items(db, owner_id) + _suggestion_items(db, owner_id)
    items.sort(key=lambda item: (item.prepared_at is not None, item.prepared_at), reverse=True)
    return items[: max(1, int(limit))]


def prepared_items(run: ScheduledTaskRun) -> list[dict[str, object]]:
    """The items a run prepared, or an empty list if the payload is unreadable."""
    try:
        payload = json.loads(run.prepared_json or "{}")
    except (TypeError, ValueError):
        return []
    items = payload.get("items") if isinstance(payload, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
