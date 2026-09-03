"""Handlers, one per task kind.

Every handler receives an open Session, the task, and a run row already
persisted, and it may write **only** those two tables. That is the
draft-and-hold boundary expressed as a code-level rule, and it is asserted by a
test that snapshots every other table's row count around a run.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.models import ScheduledTask, ScheduledTaskRun


class UnknownTaskKind(ValueError):
    """A kind with no handler. Raised rather than silently doing nothing."""


def handler_for(kind: str) -> Callable[[Session, ScheduledTask, ScheduledTaskRun], None]:
    from app.services.scheduling.handlers.digest import run_digest
    from app.services.scheduling.handlers.monitor import run_monitor
    from app.services.scheduling.handlers.reminder import run_reminder
    from app.services.scheduling.handlers.workflow import run_workflow

    handlers = {
        "reminder": run_reminder,
        "digest": run_digest,
        "monitor": run_monitor,
        "workflow": run_workflow,
        # A checklist has no schedule and is never enqueued; it exists in the
        # management view and is edited by hand.
        "checklist": _checklist_never_runs,
    }
    try:
        return handlers[kind]
    except KeyError as exc:
        raise UnknownTaskKind(f"No handler for task kind {kind!r}") from exc


def _checklist_never_runs(db: Session, task: ScheduledTask, run: ScheduledTaskRun) -> None:
    raise UnknownTaskKind("Checklists have no schedule and are never enqueued")
