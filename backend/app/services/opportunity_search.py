"""Filtered opportunity search over the fields that are actually populated (W8).

Answers "show me remote Java roles in Dallas from this month" - the retrieval
half of most of the acceptance queries in `temp162.md` §12.6. It filters on
`work_mode`, `location`, `domain`, `status`, recency, title and skills, and on
nothing else, because those are the columns that carry values.

**Filters run over normalized values, results report raw ones.** A search for
Dallas has to find `Dallas, Texas, USA` and `Dallas, TX`, which are 33 rows a
literal filter splits in two. `normalization` decides what a value means; this
module decides which rows come back. Nothing is rewritten in the database.

**A derived match says so.** `work_mode` is recorded on 741 rows and readable
out of `location` on 133 more. Both are returned, and each row carries which one
it was: a value this code worked out is a tier-2 resolution and must not read
like something the recruiter wrote.

**`employment_type` is absent on purpose.** It is empty on all 1,117 rows, so a
C2C filter would return nothing and mean nothing. `field_coverage` refuses it;
this module does not offer it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models import RecruiterOpportunity
from app.services import normalization


@dataclass(frozen=True)
class SearchFilters:
    work_mode: str = ""
    location: str = ""
    domain: str = ""
    status: str = ""
    query: str = ""
    days: int = 0

    def as_dict(self) -> dict[str, str]:
        return {
            name: str(value)
            for name, value in (
                ("work_mode", self.work_mode),
                ("location", self.location),
                ("domain", self.domain),
                ("status", self.status),
                ("query", self.query),
                ("days", self.days or ""),
            )
            if value
        }


def _matches_location(stored: str | None, wanted: str) -> bool:
    """True when a stored location names the wanted place.

    Matches on city, or on a bare state code so "TX" finds every Texas row.
    A row whose location is a work mode never matches a place - it is not a
    location that happens to be missing, it is the wrong kind of value.
    """
    place = normalization.normalize_location(stored)
    if place is None:
        return False
    target = wanted.strip().lower()
    if not target:
        return True
    if len(target) == 2 and place.state:
        return place.state.lower() == target
    wanted_place = normalization.normalize_location(wanted)
    if wanted_place and wanted_place.city.lower() == place.city.lower():
        return True
    return target in place.city.lower() or target in place.as_text().lower()


def search(
    db: Session,
    *,
    owner_id: str,
    filters: SearchFilters,
    limit: int = 15,
) -> dict[str, object]:
    """Rows matching every filter given, with what each match rested on."""
    query = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == owner_id)
    if filters.status:
        query = query.filter(RecruiterOpportunity.status == filters.status)
    if filters.days:
        cutoff = datetime.now(UTC) - timedelta(days=int(filters.days))
        query = query.filter(RecruiterOpportunity.received_at >= cutoff)
    if filters.query:
        term = f"%{filters.query.strip()}%"
        query = query.filter(
            RecruiterOpportunity.job_title.ilike(term)
            | RecruiterOpportunity.extracted_skills.ilike(term)
        )

    wanted_mode = normalization.normalize_work_mode(filters.work_mode) if filters.work_mode else None
    if filters.work_mode and wanted_mode is None:
        return {
            "error": f"Unknown work mode '{filters.work_mode}'.",
            "work_modes": list(normalization.WORK_MODES),
        }

    rows: list[dict[str, object]] = []
    derived_matches = 0
    scanned = 0
    for row in query.order_by(RecruiterOpportunity.received_at.desc()).all():
        scanned += 1
        mode = normalization.derive_work_mode(row.work_mode, row.location)
        if wanted_mode and mode.value != wanted_mode:
            continue
        if filters.location and not _matches_location(row.location, filters.location):
            continue
        if filters.domain and not normalization.domain_matches(row.domain, filters.domain):
            continue
        if mode.is_derived:
            derived_matches += 1
        place = normalization.normalize_location(row.location)
        rows.append(
            {
                "id": row.id,
                "job_title": row.job_title,
                "status": row.status,
                "work_mode": mode.value,
                # "recorded" or "location" - a derived value is not an asserted
                # one, and the difference has to survive into the answer.
                "work_mode_source": mode.source,
                "location": place.as_text() if place else None,
                "location_raw": row.location or None,
                "domains": list(normalization.normalize_domain(row.domain)),
                "received_at": row.received_at.date().isoformat() if row.received_at else None,
                "skills": row.extracted_skills,
            }
        )

    capped = max(1, min(int(limit), 50))
    result: dict[str, object] = {
        "count": len(rows),
        "scanned": scanned,
        "filters": filters.as_dict(),
        "opportunities": rows[:capped],
    }
    if derived_matches:
        result["derived_note"] = (
            f"{derived_matches} of these had no work mode recorded; it was read from "
            "the location field, which holds one on 484 rows. Those are marked "
            "work_mode_source=location."
        )
    return result
