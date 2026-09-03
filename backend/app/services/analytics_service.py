from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models import ProductivityEvent
from app.schemas import ProductivityEventResponse


def record_productivity_event(
    db: Session,
    *,
    owner_id: str,
    event_weights: dict[str, float],
    event_type: str,
    event_source: str,
    entity_id: int | None = None,
    entity_type: str = "",
    metadata: Mapping[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> ProductivityEvent:
    event = ProductivityEvent(
        owner_id=owner_id,
        event_type=event_type,
        event_source=event_source,
        entity_id=entity_id,
        entity_type=entity_type,
        weight=event_weights.get(event_type, 0.0),
        metadata_json=json.dumps(dict(metadata or {})),
        occurred_at=occurred_at or datetime.now(UTC),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def event_response(event: ProductivityEvent) -> ProductivityEventResponse:
    parsed_metadata: dict[str, object] = {}
    try:
        payload = json.loads(event.metadata_json or "{}")
        if isinstance(payload, dict):
            parsed_metadata = cast(dict[str, object], payload)
    except json.JSONDecodeError:
        parsed_metadata = {}
    return ProductivityEventResponse(
        id=event.id,
        owner_id=event.owner_id,
        event_type=event.event_type,
        event_source=event.event_source,
        entity_id=event.entity_id,
        entity_type=event.entity_type,
        weight=event.weight,
        metadata=parsed_metadata,
        occurred_at=event.occurred_at,
        created_at=event.created_at,
    )


def range_bounds(range_key: str, business_tz: ZoneInfo) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if range_key == "last_1h":
        return now - timedelta(hours=1), now
    if range_key == "current_day":
        return datetime(now.year, now.month, now.day, tzinfo=UTC), now
    if range_key == "current_week":
        now_local = datetime.now(business_tz)
        week_start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=business_tz) - timedelta(
            days=now_local.weekday()
        )
        return week_start_local.astimezone(UTC), now_local.astimezone(UTC)
    if range_key == "current_month":
        return datetime(now.year, now.month, 1, tzinfo=UTC), now
    if range_key == "current_year":
        return datetime(now.year, 1, 1, tzinfo=UTC), now
    return now - timedelta(days=365 * 5), now


def ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)



BUCKET_OPTIONS = ("five_min", "hour", "day", "month", "quarter")


def default_bucket_for_range(range_key: str) -> str:
    if range_key == "last_1h":
        return "five_min"
    if range_key == "current_day":
        return "hour"
    if range_key in ("current_week", "current_month"):
        return "day"
    if range_key == "current_year":
        return "month"
    return "quarter"


def bucket_start(ts: datetime, bucket: str) -> datetime:
    if bucket == "five_min":
        return ts.replace(minute=(ts.minute // 5) * 5, second=0, microsecond=0)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "month":
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    quarter_month = ((ts.month - 1) // 3) * 3 + 1
    return ts.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)


def add_months(ts: datetime, months: int) -> datetime:
    month_index = (ts.month - 1) + months
    return datetime(ts.year + month_index // 12, month_index % 12 + 1, 1, tzinfo=UTC)


def next_bucket(ts: datetime, bucket: str) -> datetime:
    if bucket == "five_min":
        return ts + timedelta(minutes=5)
    if bucket == "hour":
        return ts + timedelta(hours=1)
    if bucket == "day":
        return ts + timedelta(days=1)
    if bucket == "month":
        return add_months(ts, 1)
    return add_months(ts, 3)


_EMPTY_BAR = {"sent_count": 0, "failed_count": 0, "needs_review_count": 0, "recent_run_count": 0}

_EVENT_TO_COUNT = {
    "approved_sent": "sent_count",
    "failed_mapping_marked": "failed_count",
    "needs_review_marked": "needs_review_count",
    "recent_run_recorded": "recent_run_count",
}


def productivity_trend_bars(
    db: Session, *, owner_id: str, start: datetime, end: datetime, bucket: str
) -> list[dict[str, object]]:
    """Bucket productivity events into a dense series between start and end.

    Extracted from the /analytics/trend route so the route and the chat's
    get_chart tool call one implementation and cannot drift. Every bucket in
    range is present, including empty ones, so a caller can draw the axis
    without inferring gaps.
    """
    rows = (
        db.query(ProductivityEvent)
        .filter(ProductivityEvent.owner_id == owner_id)
        .filter(ProductivityEvent.occurred_at >= start, ProductivityEvent.occurred_at <= end)
        .order_by(ProductivityEvent.occurred_at.asc())
        .all()
    )

    grouped: dict[datetime, dict[str, int]] = {}
    for row in rows:
        ts = bucket_start(ensure_utc(row.occurred_at), bucket)
        counts = grouped.setdefault(ts, dict(_EMPTY_BAR))
        key = _EVENT_TO_COUNT.get(row.event_type)
        if key:
            counts[key] += 1

    bars: list[dict[str, object]] = []
    cursor = bucket_start(start, bucket)
    end_bucket = bucket_start(end, bucket)
    while cursor <= end_bucket:
        bars.append({"ts": cursor, **grouped.get(cursor, dict(_EMPTY_BAR))})
        cursor = next_bucket(cursor, bucket)
    return bars


def previous_period_sent_count(
    db: Session, *, owner_id: str, start: datetime, end: datetime
) -> int:
    """Count approved sends in the period immediately before [start, end)."""
    duration = end - start
    return (
        db.query(ProductivityEvent)
        .filter(ProductivityEvent.owner_id == owner_id)
        .filter(ProductivityEvent.event_type == "approved_sent")
        .filter(ProductivityEvent.occurred_at >= start - duration, ProductivityEvent.occurred_at < start)
        .count()
    )
