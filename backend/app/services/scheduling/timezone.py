"""The first wall-clock concept in this product.

Every timestamp in the schema is naive UTC (`UTCDateTime`), which is correct for
*records* and insufficient for *schedules*. "9am" is a wall-clock instruction; a
cron expression evaluated in UTC drifts an hour twice a year against the user's
actual morning, and the failure is invisible until someone notices the 9am
digest arriving at 10am.

Nothing here changes how timestamps are stored. It changes how schedules are
interpreted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from sqlalchemy.orm import Session

from app.models import UserSettings

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "UTC"
UTC_ZONE = ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    return bool(name) and name in available_timezones()


def zone_for(name: str | None) -> ZoneInfo:
    """Resolve an IANA name, falling back to UTC. Never raises.

    A bad zone on one task must not stop the sweep from processing every other
    task, so this degrades rather than propagating.
    """
    candidate = (name or "").strip()
    if not candidate:
        return UTC_ZONE
    try:
        return ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        logger.warning("scheduling_unknown_timezone name=%s falling_back=UTC", candidate)
        return UTC_ZONE


def user_zone(db: Session, *, owner_id: str) -> ZoneInfo:
    """The owner's zone, falling back to UTC. Never raises."""
    try:
        row = (
            db.query(UserSettings.timezone)
            .filter(UserSettings.owner_id == owner_id)
            .first()
        )
    except Exception:
        logger.exception("scheduling_user_zone_lookup_failed owner=%s", owner_id)
        return UTC_ZONE
    return zone_for(row[0] if row else None)


def to_user_local(moment: datetime, zone: ZoneInfo) -> datetime:
    """A UTC instant as the same instant in the user's zone. Display only."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(zone)


def from_user_local(naive_local: datetime, zone: ZoneInfo) -> datetime:
    """A wall-clock time in the user's zone -> the UTC instant it names.

    The two DST edge cases are resolved explicitly rather than left to whatever
    the library happens to default to:

    * **Ambiguous** (the repeated hour when DST ends) resolves to the FIRST
      occurrence - the earlier instant, so a 1:30am task runs once, on time.
    * **Non-existent** (the skipped hour when DST begins) shifts FORWARD to the
      first valid instant, so a 2:30am task runs at 3:00am rather than never.

    "The 2:30am digest did not run in March" is a bug report nobody enjoys
    diagnosing, and silence is the failure mode a naive implementation picks.
    """
    local = naive_local.replace(tzinfo=None)
    first = local.replace(tzinfo=zone, fold=0)
    second = local.replace(tzinfo=zone, fold=1)

    if first.utcoffset() != second.utcoffset():
        # Offsets differ, so this wall-clock time is either repeated or skipped.
        # A repeated hour round-trips back to itself; a skipped one does not.
        if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == local:
            return first.astimezone(UTC)
        # Skipped: step forward by the size of the jump to the first valid time.
        gap = abs(second.utcoffset() - first.utcoffset()) or timedelta(hours=1)
        shifted = (local + gap).replace(tzinfo=zone, fold=0)
        return shifted.astimezone(UTC)

    return first.astimezone(UTC)
