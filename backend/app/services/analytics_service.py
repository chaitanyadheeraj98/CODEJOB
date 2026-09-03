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

