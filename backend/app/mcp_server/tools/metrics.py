from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.config import settings
from app.db import SessionLocal
from app.mcp_server.tools import provenance
from app.models import (
    APPLICATION_CLOSED_STATUS_VALUES,
    Application,
    RecruiterEmail,
)
from app.services import analytics_service, application_service, resume_tracking_service

# The same range vocabulary the dashboard's own selector uses. Sharing
# range_bounds is deliberate: a metric card and the Run Queue page must not
# disagree about what "this month" means.
RANGE_OPTIONS = ("last_1h", "current_day", "current_week", "current_month", "current_year", "last_5y")
BUSINESS_TZ = ZoneInfo("America/Chicago")

MAX_STALE_DAYS = 365


def _bounds(range_key: str) -> tuple[datetime, datetime]:
    return analytics_service.range_bounds(range_key, BUSINESS_TZ)


def _pipeline_summary(db, range_key: str, days: int) -> dict[str, object]:
    start, end = _bounds(range_key)
    summary = application_service.dashboard_summary(db, settings.owner_id)
    total = int(
        db.query(func.count(Application.id))
        .filter(Application.owner_id == settings.owner_id, Application.deleted_at.is_(None))
        .scalar()
        or 0
    )
    tracked = {"page": "application_tracking", "tab": "tracked", "filters": {}}
    cards = [
        {"label": "Due today", "value": summary["due_today"], "unit": "", "delta": None, "drill_to": tracked},
        {"label": "Waiting on recruiter", "value": summary["waiting_on_recruiter"], "unit": "", "delta": None, "drill_to": tracked},
        {"label": "Interviews", "value": summary["interviews"], "unit": "", "delta": None, "drill_to": tracked},
        {"label": "Closed, last 14 days", "value": summary["closed_recent"], "unit": "", "delta": None, "drill_to": None},
    ]
    return {
        "title": "Application pipeline",
        "cards": cards,
        "provenance": provenance.block(
            metric="Application pipeline",
            source="get_metrics/pipeline_summary",
            row_count=total,
            start=start,
            end=end,
            filters={"range": range_key},
            assumptions=[
                "Deleted applications are excluded.",
                "'Due today' counts open applications whose next action falls today (UTC).",
                "Every card here is a live count and ignores the selected range.",
            ],
        ),
    }


def _candidate_queue(db, range_key: str, days: int) -> dict[str, object]:
    start, end = _bounds(range_key)
    rows = (
        db.query(RecruiterEmail.state, func.count(RecruiterEmail.id))
        .filter(
            RecruiterEmail.owner_id == settings.owner_id,
            RecruiterEmail.created_at >= start,
            RecruiterEmail.created_at <= end,
        )
        .group_by(RecruiterEmail.state)
        .all()
    )
    counts = {str(state): int(count) for state, count in rows}
    labels = {
        "needs_review": "Needs review",
        "approved_sent": "Approved and sent",
        "failed": "Failed mapping",
        "rejected": "Rejected",
        "auto_rejected": "Auto-rejected",
    }
    pages = {"needs_review": "needs_review", "approved_sent": "sent_items", "failed": "failed_mapping"}
    order = ("needs_review", "approved_sent", "failed", "rejected", "auto_rejected")
    cards = [
        {
            "label": labels.get(state, state.replace("_", " ").capitalize()),
            "value": counts.get(state, 0),
            "unit": "",
            "delta": None,
            "drill_to": {"page": pages[state], "tab": None, "filters": {}} if state in pages else None,
        }
        for state in order
        if state in counts or state in pages
    ]
    return {
        "title": "Candidate queue",
        "cards": cards,
        "provenance": provenance.block(
            metric="Candidate emails by state",
            source="get_metrics/candidate_queue",
            row_count=sum(counts.values()),
            start=start,
            end=end,
            filters={"range": range_key},
            assumptions=[
                "Counted by the date the email was received, not the date it was actioned.",
                "A state with no rows in this range and no queue page of its own is omitted.",
            ],
        ),
    }


def _stale_applications(db, range_key: str, days: int) -> dict[str, object]:
    cutoff = datetime.now(UTC) - timedelta(days=days)
    open_only = (
        Application.owner_id == settings.owner_id,
        Application.deleted_at.is_(None),
        Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
    )
    total_open = int(db.query(func.count(Application.id)).filter(*open_only).scalar() or 0)
    # status_changed_at, never updated_at: updated_at carries onupdate=utc_now,
    # so attaching a note or an event resets it and "no movement in 14 days"
    # would silently exclude every application the user merely edited.
    stale = int(
        db.query(func.count(Application.id))
        .filter(*open_only, Application.status_changed_at < cutoff)
        .scalar()
        or 0
    )
    return {
        "title": f"Applications with no movement in {days} days",
        "cards": [
            {
                "label": f"Stale ({days}d+)",
                "value": stale,
                "unit": "",
                "delta": None,
                "drill_to": {"page": "application_tracking", "tab": "tracked", "filters": {}},
            },
            {"label": "Open applications", "value": total_open, "unit": "", "delta": None, "drill_to": None},
        ],
        "provenance": provenance.block(
            metric="Stale applications",
            source="get_metrics/stale_applications",
            row_count=total_open,
            start=cutoff,
            end=datetime.now(UTC),
            filters={"days": str(days)},
            assumptions=[
                "Measured from the last status change, not the last edit.",
                "Closed, rejected, withdrawn and duplicate applications are excluded.",
                "Deleted applications are excluded.",
            ],
        ),
    }


def _resume_performance(db, range_key: str, days: int) -> dict[str, object]:
    rows = resume_tracking_service.resume_performance_summary(
        db, owner_id=settings.owner_id, sort="acceptance_desc", combined=True
    )
    cards = [
        {
            "label": str(row["resume"].file_name),
            "value": int(row["submission_count"]),
            "unit": "submissions",
            "delta": None,
            "drill_to": {"page": "resume_tracking", "tab": "submissions", "filters": {}},
        }
        for row in rows[:6]
    ]
    return {
        "title": "Resume performance",
        "cards": cards,
        "provenance": provenance.block(
            metric="Submissions per resume",
            source="get_metrics/resume_performance",
            row_count=len(rows),
            filters={"sort": "acceptance_desc"},
            assumptions=[
                "Counts every submission ever recorded; the selected range does not apply.",
                "Only the six highest-acceptance resumes are shown.",
            ],
        ),
    }


METRICS = {
    "pipeline_summary": _pipeline_summary,
    "candidate_queue": _candidate_queue,
    "stale_applications": _stale_applications,
    "resume_performance": _resume_performance,
}


def get_metrics(metric: str, range: str = "current_month", days: int = 14) -> dict[str, object]:
    """Return KPI figures for one named metric, with the query that produced them.

    Call this for questions about counts, rates, or "how many" - "how many
    candidates are waiting for me", "what is in my pipeline", "which
    applications have gone quiet". It draws labelled cards showing the range
    and filters used, so do not restate the numbers as prose afterwards.

    Valid metrics: pipeline_summary (applications by stage grouping),
    candidate_queue (candidate emails by state), stale_applications (no status
    change in `days` days), resume_performance (submissions per resume).

    `range` is one of last_1h, current_day, current_week, current_month,
    current_year, last_5y. Every displayed value is computed here; you supply
    only the metric name, the range, and the day count, and never a figure of
    your own.
    """
    reader = METRICS.get(metric)
    if reader is None:
        return {"error": f"Unknown metric '{metric}'.", "metrics": sorted(METRICS)}
    if range not in RANGE_OPTIONS:
        return {"error": f"Unknown range '{range}'.", "ranges": list(RANGE_OPTIONS)}
    window = max(1, min(int(days), MAX_STALE_DAYS))

    db = SessionLocal()
    try:
        payload = reader(db, range, window)
    finally:
        db.close()

    return {"action": "render_metric_cards", **payload}
