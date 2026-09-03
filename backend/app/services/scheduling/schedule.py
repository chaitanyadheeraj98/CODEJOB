"""When does this schedule fire next, and how do you say that in English?

Both questions are answered in the task's own zone and converted back to UTC,
so "0 9 * * 1-5" is 9am local across a DST boundary rather than a fixed UTC
offset that drifts twice a year.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from croniter import CroniterBadCronError, croniter

from app.services.scheduling.timezone import from_user_local, to_user_local, zone_for

logger = logging.getLogger(__name__)

# The finest granularity the sweep can honour. A schedule asking for anything
# faster is refused rather than silently rounded up - accepting an expression
# you will not obey is worse than saying no.
SWEEP_FLOOR_MINUTES = 5

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_CRON_FIELD_COUNT = 5


class InvalidSchedule(ValueError):
    """A schedule this system cannot honour, with a reason the user can act on."""


@dataclass(frozen=True)
class ScheduleSpec:
    schedule_kind: str  # once | recurring | condition | none
    cron_expression: str = ""
    run_at: datetime | None = None
    timezone: str = "UTC"


def validate_cron(expression: str) -> str:
    """Normalize a 5-field cron expression or raise InvalidSchedule.

    Rejects seconds-precision and @reboot-style shorthands: the sweep's finest
    granularity is SWEEP_FLOOR_MINUTES, so a 6-field or per-second expression
    is a promise this scheduler cannot keep.
    """
    text = (expression or "").strip()
    if not text:
        raise InvalidSchedule("A recurring task needs a cron expression.")
    if text.startswith("@"):
        raise InvalidSchedule(
            "Shorthand expressions like @reboot are not supported. "
            "Use a 5-field cron expression, for example '0 9 * * 1-5'."
        )
    fields = text.split()
    if len(fields) != _CRON_FIELD_COUNT:
        raise InvalidSchedule(
            f"Use a standard 5-field cron expression (minute hour day month weekday); "
            f"got {len(fields)} field(s)."
        )
    try:
        croniter(text)
    except (CroniterBadCronError, ValueError) as exc:
        raise InvalidSchedule(f"Not a valid cron expression: {exc}") from exc

    _reject_sub_floor(text)
    return text


def _reject_sub_floor(expression: str) -> None:
    """Refuse anything firing more often than the sweep can notice."""
    base = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)
    cursor = croniter(expression, base)
    first = cursor.get_next(datetime)
    second = cursor.get_next(datetime)
    gap_minutes = (second - first).total_seconds() / 60
    if gap_minutes < SWEEP_FLOOR_MINUTES:
        raise InvalidSchedule(
            f"That fires every {gap_minutes:.0f} minutes, but scheduled work is checked "
            f"at most every {SWEEP_FLOOR_MINUTES} minutes. Choose a slower schedule."
        )


def next_run_after(spec: ScheduleSpec, after: datetime) -> datetime | None:
    """The next UTC instant this schedule fires strictly after `after`.

    Returns None for a one-time schedule that has already run, and for the
    `condition` and `none` kinds, which have no clock trigger.
    """
    moment = after if after.tzinfo is not None else after.replace(tzinfo=UTC)

    if spec.schedule_kind == "once":
        if spec.run_at is None:
            return None
        run_at = spec.run_at if spec.run_at.tzinfo is not None else spec.run_at.replace(tzinfo=UTC)
        return run_at if run_at > moment else None

    if spec.schedule_kind != "recurring":
        return None

    try:
        validate_cron(spec.cron_expression)
    except InvalidSchedule:
        # A task with an unusable expression must not stop the sweep. It simply
        # has no next run, and the management view shows it standing still.
        logger.warning("scheduling_unusable_cron expression=%s", spec.cron_expression)
        return None

    zone = zone_for(spec.timezone)
    # Iterate in local wall-clock time, then convert. Doing it the other way
    # round is exactly the drift this module exists to prevent.
    local_after = to_user_local(moment, zone).replace(tzinfo=None)
    cursor = croniter(spec.cron_expression, local_after)
    for _ in range(8):
        candidate_local = cursor.get_next(datetime)
        candidate_utc = from_user_local(candidate_local, zone)
        # A skipped DST hour shifts forward, which can land on or before the
        # instant we started from; keep walking until it is genuinely later.
        if candidate_utc > moment:
            return candidate_utc
    return None


def describe(spec: ScheduleSpec) -> str:
    """Plain-language trigger for the management view and the preview card.

    The zone is always named. A trigger the user cannot verify is not the
    transparency the spec asks for - "every weekday at 9:00 AM" is a different
    promise in two different zones.
    """
    zone_name = (spec.timezone or "UTC").strip() or "UTC"

    if spec.schedule_kind == "none":
        return "No schedule - runs only when you open it"
    if spec.schedule_kind == "condition":
        return f"When its condition is met (checked on the sweep, {zone_name})"
    if spec.schedule_kind == "once":
        if spec.run_at is None:
            return "Once, at a time not yet set"
        local = to_user_local(spec.run_at, zone_for(zone_name))
        # Built by hand rather than with %-d / %-I, which are glibc-only and
        # raise on Windows - the dashboard's dev host.
        stamp = f"{local.strftime('%A')} {local.day} {local.strftime('%B')} {local.year}"
        return f"Once, on {stamp} at {_clock(local.hour, local.minute)} ({zone_name})"
    if spec.schedule_kind != "recurring":
        return f"Unrecognized schedule ({zone_name})"

    return f"{_describe_cron(spec.cron_expression)} ({zone_name})"


def _describe_cron(expression: str) -> str:
    text = (expression or "").strip()
    fields = text.split()
    if len(fields) != _CRON_FIELD_COUNT:
        return f"On the schedule '{text}'"
    minute, hour, day_of_month, month, day_of_week = fields

    if not (minute.isdigit() and hour.isdigit()):
        # Anything with a step or a list is stated verbatim rather than
        # paraphrased. A wrong plain-language rendering is worse than a raw one.
        return f"On the schedule '{text}'"

    clock = _clock(int(hour), int(minute))

    if day_of_week == "1-5" and day_of_month == "*" and month == "*":
        return f"Every weekday at {clock}"
    if day_of_week in ("*", "?") and day_of_month == "*" and month == "*":
        return f"Every day at {clock}"
    if day_of_week.isdigit() and day_of_month == "*" and month == "*":
        index = int(day_of_week) % 7
        return f"Every {_DAY_NAMES[(index - 1) % 7]} at {clock}"
    if day_of_month.isdigit() and month == "*":
        return f"On day {int(day_of_month)} of each month at {clock}"
    return f"On the schedule '{text}'"


def _clock(hour: int, minute: int) -> str:
    suffix = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def granularity_note() -> str:
    """The caveat the preview card must show rather than let the user discover."""
    return (
        f"Scheduled work is checked every few minutes, so a run can start up to "
        f"{SWEEP_FLOOR_MINUTES} minutes after its scheduled time."
    )
