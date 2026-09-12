"""F4 / §12.4: what is happening across the app, without reading anyone's mail.

Answers the questions §12.2 poses - *"the assistant is slow for me"* - from
columns that already exist: turns per hour, p50/p95 `duration_ms` and
`time_to_first_token_ms`, failure codes by frequency, the `admission_rejected`
count, and each of those per user.

**§12.1 is enforced by the column list, not by intention.** Exactly five
columns are read, every one an identifier, a timing or a code, and a test
asserts the set. `ChatTurn` carries no message content by construction, but
"the table happens to be safe today" is not a control; naming the columns is.

Bounded like `ChatService.telemetry_summary`, and for the same reason: this
feeds a page someone will leave open, so its cost must not grow with the table.
A window, a row cap, and a `truncated` flag when the cap bites - because a
number that is quietly wrong is worse than one that admits it is partial.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import ChatTurn

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_HOURS = 24
MAX_WINDOW_HOURS = 24 * 7

#: The percentiles come from a sample of at most this many of the most recent
#: turns. 5,000 rows of five small columns is nothing to fetch; an unbounded
#: `SELECT` behind an admin page is a future outage.
MAX_ROWS = 5_000

#: Beyond this many accounts the per-user table stops being read and starts
#: being scrolled. Ordered by turns, so the busiest are the ones kept.
MAX_USERS = 50

#: The only columns this module may read. §12.1: identifiers, timings, counts
#: and error codes. Asserted by a test - see `test_admin_observability`.
READS_COLUMNS = ("owner_id", "created_at", "duration_ms", "time_to_first_token_ms", "failure_code")

ADMISSION_REJECTED = "admission_rejected"


@dataclass(frozen=True)
class _Row:
    owner_id: str
    created_at: datetime
    duration_ms: int | None
    time_to_first_token_ms: int | None
    failure_code: str | None


def _percentiles(values: list[int]) -> dict[str, int | None]:
    """p50 and p95 by index, not interpolation.

    The same choice `telemetry_summary` makes: with a capped sample this is the
    honest "95% of turns were at least this fast", and it needs no numpy.
    """
    if not values:
        return {"p50": None, "p95": None}
    ordered = sorted(values)
    return {
        "p50": ordered[len(ordered) // 2],
        "p95": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
    }


def _metric(rows: list[_Row], attribute: str) -> dict[str, int | None]:
    """Percentiles over the turns that *have* the metric.

    `time_to_first_token_ms` is null for a turn that failed before the first
    token. Counting those as zero would report the assistant getting faster the
    more often it broke.
    """
    return _percentiles([
        value for value in (getattr(row, attribute) for row in rows) if value is not None
    ])


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; Postgres does not."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hour_buckets(rows: list[_Row], *, since: datetime, until: datetime) -> list[dict[str, object]]:
    """One entry per hour in the window, including the empty ones.

    Gaps are filled rather than omitted so that "nothing happened at 04:00"
    reads as a zero instead of a missing bucket that a chart would smooth over.
    """
    counts: Counter[datetime] = Counter(
        _as_utc(row.created_at).replace(minute=0, second=0, microsecond=0) for row in rows
    )
    buckets: list[dict[str, object]] = []
    hour = since.replace(minute=0, second=0, microsecond=0)
    while hour <= until:
        buckets.append({"hour": hour, "turns": counts.get(hour, 0)})
        hour += timedelta(hours=1)
    return buckets


def summarise(db: Session, *, window_hours: int = DEFAULT_WINDOW_HOURS) -> dict[str, object]:
    """The whole page in one query.

    Never raises: an observability page that 500s is reporting on an outage it
    caused. An empty summary is the honest answer when the query fails.
    """
    window_hours = max(1, min(int(window_hours), MAX_WINDOW_HOURS))
    until = datetime.now(UTC)
    since = until - timedelta(hours=window_hours)

    try:
        fetched = (
            db.query(
                ChatTurn.owner_id,
                ChatTurn.created_at,
                ChatTurn.duration_ms,
                ChatTurn.time_to_first_token_ms,
                ChatTurn.failure_code,
            )
            .filter(ChatTurn.created_at >= since)
            .order_by(ChatTurn.created_at.desc())
            .limit(MAX_ROWS + 1)
            .all()
        )
    except Exception:
        logger.warning("Could not summarise observability", exc_info=True)
        fetched = []

    truncated = len(fetched) > MAX_ROWS
    rows = [_Row(*row) for row in fetched[:MAX_ROWS]]

    codes = Counter(row.failure_code for row in rows if row.failure_code)

    by_owner: dict[str, list[_Row]] = defaultdict(list)
    for row in rows:
        by_owner[row.owner_id].append(row)

    per_user = sorted(
        (
            {
                "owner_id": owner_id,
                "turns": len(owned),
                "failed": sum(1 for row in owned if row.failure_code),
                "admission_rejected": sum(
                    1 for row in owned if row.failure_code == ADMISSION_REJECTED
                ),
                "duration_ms": _metric(owned, "duration_ms"),
                "time_to_first_token_ms": _metric(owned, "time_to_first_token_ms"),
            }
            for owner_id, owned in by_owner.items()
        ),
        key=lambda entry: (-entry["turns"], entry["owner_id"]),
    )[:MAX_USERS]

    return {
        "window_hours": window_hours,
        "since": since,
        "until": until,
        # True when the row cap bit, so a partial percentile is never mistaken
        # for a complete one.
        "truncated": truncated,
        "turns": len(rows),
        "failed": sum(1 for row in rows if row.failure_code),
        "admission_rejected": codes.get(ADMISSION_REJECTED, 0),
        "duration_ms": _metric(rows, "duration_ms"),
        "time_to_first_token_ms": _metric(rows, "time_to_first_token_ms"),
        "turns_per_hour": _hour_buckets(rows, since=since, until=until),
        "failure_codes": [
            {"code": code, "turns": count} for code, count in codes.most_common()
        ],
        "per_user": per_user,
    }
