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
    # Corpus coverage of every column the aggregate reads. A trend line over a
    # sparse column reads as a trend whatever the caption says, so the number
    # travels with the chart rather than being left to the caller to recall.
    coverage: list[dict[str, object]] = field(default_factory=list)


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
    coverage: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Build the provenance block attached to every analysis payload.

    `row_count` is the number of rows the aggregate actually read, not the
    number displayed. `assumptions` must state every exclusion the query makes:
    if it filters deleted_at IS NULL or a status set, that is an assumption and
    the user cannot see it any other way.

    `coverage` states how populated each source column is, corpus-wide - not over
    the rows this aggregate selected, which is self-selected and would flatter a
    sparse column. An aggregate whose source column cannot carry a claim is not
    drawn at all; see `field_coverage.aggregate_refusal`.
    """
    return {
        "metric": metric,
        "source": source,
        "row_count": int(row_count),
        "date_range": date_range(start, end),
        "filters": dict(filters or {}),
        "assumptions": list(assumptions or []),
        "coverage": list(coverage or []),
    }


# Defined in app/services/evidence.py and re-exported here so tools keep one
# import site. See that module for why it cannot live in this package.
from app.services.evidence import (  # noqa: E402
    CONFIDENCE_LEVELS,
    INFERENCE_ASSUMPTION,
    MATCH_KINDS,
    EvidenceEntry,
)


def inference_block(
    *,
    metric: str,
    source: str,
    row_count: int,
    confidence: str,
    score: float,
    evidence: list[EvidenceEntry],
    semantic_available: bool,
    start: datetime | None = None,
    end: datetime | None = None,
    filters: dict[str, str] | None = None,
    assumptions: list[str] | None = None,
) -> dict[str, object]:
    """Provenance for a *derived* claim.

    Everything block() carries, plus the three things an inference owes the
    user: how sure, why, and on what basis. INFERENCE_ASSUMPTION is always the
    first assumption - the user is told structurally that this is not a
    recorded fact, rather than being told so in model prose that nothing checks.

    Raises ValueError rather than emitting a claim that cannot be inspected: an
    inference with no evidence must be impossible to construct, not merely
    discouraged. The frontend drops such a payload too, but a payload that
    reaches the client malformed has already been a bug for one hop.
    """
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    if not evidence:
        raise ValueError("an inference with no evidence cannot be displayed")
    numeric_score = float(score)
    if not 0.0 <= numeric_score <= 1.0:
        raise ValueError(f"score must be within [0.0, 1.0], got {numeric_score!r}")

    payload = block(
        metric=metric,
        source=source,
        row_count=row_count,
        start=start,
        end=end,
        filters=filters,
        assumptions=[INFERENCE_ASSUMPTION, *(assumptions or [])],
    )
    payload["confidence"] = confidence
    payload["score"] = round(numeric_score, 4)
    payload["evidence"] = [entry.as_dict() for entry in evidence]
    payload["semantic_available"] = bool(semantic_available)
    return payload
