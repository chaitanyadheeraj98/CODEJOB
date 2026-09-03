from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Provenance:
    """What produced a displayed number.

    Every analysis payload carries one. The frontend refuses to render a
    visualization whose provenance is missing or malformed - a chart with a
    caveat is still a chart, so the control has to be "renders nothing", not
    "renders with a warning".
    """

    metric: str
    source: str
    row_count: int
    date_range: dict[str, str | None] = field(default_factory=dict)
    filters: dict[str, str] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)


def date_range(start: datetime | None, end: datetime | None) -> dict[str, str | None]:
    """Format a bounds pair for display, keeping None as an open end."""
    return {
        "from": start.date().isoformat() if start else None,
        "to": end.date().isoformat() if end else None,
    }


def block(
    *,
    metric: str,
    source: str,
    row_count: int,
    start: datetime | None = None,
    end: datetime | None = None,
    filters: dict[str, str] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, object]:
    """Build the provenance block attached to every analysis payload.

    `row_count` is the number of rows the aggregate actually read, not the
    number displayed. `assumptions` must state every exclusion the query makes:
    if it filters deleted_at IS NULL or a status set, that is an assumption and
    the user cannot see it any other way.
    """
    return {
        "metric": metric,
        "source": source,
        "row_count": int(row_count),
        "date_range": date_range(start, end),
        "filters": dict(filters or {}),
        "assumptions": list(assumptions or []),
    }
