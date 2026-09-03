from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.config import settings
from app.db import SessionLocal
from app.mcp_server.tools import provenance
from app.models import (
    APPLICATION_STATUS_VALUES,
    Application,
    RecruiterEmail,
)
from app.services import analytics_service, resume_tracking_service

BUSINESS_TZ = ZoneInfo("America/Chicago")
RANGE_OPTIONS = ("last_1h", "current_day", "current_week", "current_month", "current_year", "last_5y")
BUCKET_OPTIONS = analytics_service.BUCKET_OPTIONS

CHART_TYPES = ("activity_trend", "candidate_states", "resume_funnel", "application_pipeline")

_STATE_LABELS = {
    "needs_review": "Needs review",
    "approved_sent": "Approved and sent",
    "failed": "Failed mapping",
    "rejected": "Rejected",
    "auto_rejected": "Auto-rejected",
}
_STATE_PAGES = {"needs_review": "needs_review", "approved_sent": "sent_items", "failed": "failed_mapping"}

_STATUS_LABELS = {value: value.replace("_", " ").capitalize() for value in APPLICATION_STATUS_VALUES}


def _activity_trend(db, range_key: str, bucket: str, subject_id: int) -> dict[str, object]:
    start, end = analytics_service.range_bounds(range_key, BUSINESS_TZ)
    start = analytics_service.ensure_utc(start)
    end = analytics_service.ensure_utc(end)
    bars = analytics_service.productivity_trend_bars(
        db, owner_id=settings.owner_id, start=start, end=end, bucket=bucket
    )
    series = [
        {
            "label": row["ts"].date().isoformat() if bucket in ("day", "month", "quarter") else row["ts"].isoformat(timespec="minutes"),
            "value": int(row["sent_count"]),
            "rate_of_previous": None,
            "drill_to": {"page": "sent_items", "tab": None, "filters": {}},
        }
        for row in bars
    ]
    return {
        "title": "Approved sends",
        "series": series,
        "provenance": provenance.block(
            metric="Approved sends",
            source="get_chart/activity_trend",
            row_count=len(bars),
            start=start,
            end=end,
            filters={"range": range_key, "bucket": bucket},
            assumptions=[
                "Counts the approved_sent productivity event, not messages in the mailbox.",
                "Empty buckets are shown as zero rather than omitted.",
            ],
        ),
    }


def _candidate_states(db, range_key: str, bucket: str, subject_id: int) -> dict[str, object]:
    start, end = analytics_service.range_bounds(range_key, BUSINESS_TZ)
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
    series = [
        {
            "label": _STATE_LABELS.get(state, state.replace("_", " ").capitalize()),
            "value": count,
            "rate_of_previous": None,
            "drill_to": {"page": _STATE_PAGES[state], "tab": None, "filters": {}} if state in _STATE_PAGES else None,
        }
        for state, count in sorted(counts.items(), key=lambda item: -item[1])
    ]
    return {
        "title": "Candidate emails by state",
        "series": series,
        "provenance": provenance.block(
            metric="Candidate emails by state",
            source="get_chart/candidate_states",
            row_count=sum(counts.values()),
            start=start,
            end=end,
            filters={"range": range_key},
            assumptions=["Counted by the date the email was received, not the date it was actioned."],
        ),
    }


def _resume_funnel(db, range_key: str, bucket: str, subject_id: int) -> dict[str, object]:
    metrics = resume_tracking_service.combined_resume_funnel_metrics(
        db, owner_id=settings.owner_id, resume_asset_id=subject_id
    )
    total = int(metrics.get("total_submissions", 0) or 0)
    # The service stores rates (0-1 of total submissions), not per-stage counts.
    # Multiplying back gives the stage count it was derived from; rounding is
    # safe because _rate keeps four decimals and totals here are small.
    stages = (
        ("view_rate", "Viewed"),
        ("shortlist_rate", "Shortlisted"),
        ("interview_rate", "Interview"),
        ("offer_rate", "Offered"),
        ("hire_rate", "Hired"),
    )
    series: list[dict[str, object]] = [{
        "label": "Submitted",
        "value": total,
        "rate_of_previous": None,
        "drill_to": {"page": "resume_tracking", "tab": "submissions", "filters": {}},
    }]
    previous = total
    for key, label in stages:
        value = round(float(metrics.get(key, 0.0) or 0.0) * total)
        series.append({
            "label": label,
            "value": value,
            "rate_of_previous": round(value / previous * 100, 1) if previous else None,
            "drill_to": {"page": "resume_tracking", "tab": "submissions", "filters": {}},
        })
        previous = value
    return {
        "title": "Resume funnel",
        "series": series,
        "provenance": provenance.block(
            metric="Resume funnel",
            source="get_chart/resume_funnel",
            row_count=total,
            filters={"resume_asset_id": str(subject_id)},
            assumptions=[
                "Counts every submission of this resume ever recorded; the selected range does not apply.",
                "Stage counts are derived from the stored stage rates and the submission total.",
                "A submission counts towards every stage it reached, so the stages are cumulative.",
            ],
        ),
    }


def _application_pipeline(db, range_key: str, bucket: str, subject_id: int) -> dict[str, object]:
    rows = (
        db.query(Application.status, func.count(Application.id))
        .filter(Application.owner_id == settings.owner_id, Application.deleted_at.is_(None))
        .group_by(Application.status)
        .all()
    )
    counts = {str(status): int(count) for status, count in rows}
    # Ordered by APPLICATION_STATUS_VALUES, which is semantically ordered and
    # already treated as such by application_service's stage comparison.
    series = [
        {
            "label": _STATUS_LABELS.get(status, status),
            "value": counts.get(status, 0),
            "rate_of_previous": None,
            "drill_to": {"page": "application_tracking", "tab": "tracked", "filters": {}},
        }
        for status in APPLICATION_STATUS_VALUES
        if counts.get(status)
    ]
    return {
        "title": "Applications by stage",
        "series": series,
        "provenance": provenance.block(
            metric="Applications by stage",
            source="get_chart/application_pipeline",
            row_count=sum(counts.values()),
            filters={},
            assumptions=[
                "Deleted applications are excluded.",
                "Stages are ordered by the pipeline's own status order, not by count.",
                "A stage with no applications is omitted.",
            ],
        ),
    }


CHARTS = {
    "activity_trend": _activity_trend,
    "candidate_states": _candidate_states,
    "resume_funnel": _resume_funnel,
    "application_pipeline": _application_pipeline,
}

# Only the bucketed chart cares about `bucket`; validating it for the others
# would refuse a harmless argument.
_BUCKETED = ("activity_trend",)


def get_chart(chart: str, range: str = "current_month", bucket: str = "", subject_id: int = 0) -> dict[str, object]:
    """Draw one of the supported charts from data computed on the server.

    Call this when the user asks to see something "over time", "by stage", a
    breakdown, or a funnel.

    Valid charts: activity_trend (approved sends per bucket), candidate_states
    (candidate emails by state), resume_funnel (submitted through hired for one
    resume - pass its id as subject_id), application_pipeline (applications by
    stage).

    `range` is one of last_1h, current_day, current_week, current_month,
    current_year, last_5y. `bucket` applies only to activity_trend and defaults
    sensibly from the range; valid values are five_min, hour, day, month,
    quarter. You supply no values - every number is computed here.
    """
    reader = CHARTS.get(chart)
    if reader is None:
        return {"error": f"Unknown chart '{chart}'.", "charts": list(CHART_TYPES)}
    if range not in RANGE_OPTIONS:
        return {"error": f"Unknown range '{range}'.", "ranges": list(RANGE_OPTIONS)}

    resolved_bucket = (bucket or "").strip() or analytics_service.default_bucket_for_range(range)
    if chart in _BUCKETED and resolved_bucket not in BUCKET_OPTIONS:
        # Refused with the valid list, never silently corrected.
        return {"error": f"Unknown bucket '{bucket}'.", "buckets": list(BUCKET_OPTIONS)}

    if chart == "resume_funnel" and int(subject_id) <= 0:
        return {"status": "missing_fields", "missing": ["subject_id"], "detail": "resume_funnel needs a resume id."}

    db = SessionLocal()
    try:
        payload = reader(db, range, resolved_bucket, int(subject_id))
    finally:
        db.close()

    series = payload["series"]
    assert isinstance(series, list)
    # Sent server-side so a truncated series cannot silently rescale itself.
    max_value = max((int(point["value"]) for point in series), default=0)

    return {
        "action": "render_chart",
        "chart_type": chart,
        "max_value": max_value,
        **payload,
    }
