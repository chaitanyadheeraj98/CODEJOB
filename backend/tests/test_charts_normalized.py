"""W11 — the two charts that read normalized values.

The rule these pin: a chart reports the coverage of what its column *means*, not
of what the column contains. `location` is populated on 98.9% of production rows
and about half of that is a work mode, so a map captioned 98.9% would be true and
misleading.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.mcp_server.tools import charts
from app.models import Base, RecruiterOpportunity

OWNER_ID = "default-owner"
NOW = datetime.now(UTC)


@pytest.fixture()
def db(monkeypatch: pytest.MonkeyPatch) -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    monkeypatch.setattr(charts, "SessionLocal", lambda: session)
    monkeypatch.setattr(charts.settings, "owner_id", OWNER_ID)
    yield session
    session.close()


def _opp(db: Session, tag: str, **overrides) -> None:
    values = {
        "owner_id": OWNER_ID, "recruiter_number_id": 1, "gmail_message_id": f"m-{tag}",
        "job_title": "Java Developer", "status": "New", "received_at": NOW,
        "location": "", "work_mode": "", "domain": "",
    }
    values.update(overrides)
    db.add(RecruiterOpportunity(**values))


def _coverage(payload: dict, label_starts: str) -> dict:
    return next(c for c in payload["provenance"]["coverage"] if c["label"].startswith(label_starts))


def test_both_charts_are_registered(db: Session) -> None:
    assert "role_demand" in charts.CHART_TYPES
    assert "location_by_work_mode" in charts.CHART_TYPES


def test_role_demand_groups_titles_into_families(db: Session) -> None:
    """645 distinct titles do not chart. "Java Developer" and "Sr Java
    Developer" are one bar."""
    _opp(db, "a", job_title="Java Developer")
    _opp(db, "b", job_title="Sr Java Developer")
    _opp(db, "c", job_title="Senior Java Developer")
    _opp(db, "d", job_title="QA Automation Engineer")
    db.commit()

    series = charts.get_chart("role_demand", range="current_year")["series"]

    assert series[0]["label"] == "Java"
    assert series[0]["value"] == 3


def test_a_title_naming_two_families_is_counted_in_both(db: Session) -> None:
    """And the assumption says so, because the bars then sum above the row
    count and a reader is owed that before they add them up."""
    _opp(db, "a", job_title="Java Full Stack Developer")
    db.commit()

    payload = charts.get_chart("role_demand", range="current_year")
    labels = {point["label"]: point["value"] for point in payload["series"]}

    assert labels == {"Java": 1, "Full stack": 1}
    assert any("counted in both" in text for text in payload["provenance"]["assumptions"])


def test_role_coverage_counts_titles_that_resolved_not_titles_that_exist(db: Session) -> None:
    _opp(db, "a", job_title="Java Developer")
    _opp(db, "b", job_title="Widget Polisher")
    db.commit()

    coverage = _coverage(charts.get_chart("role_demand", range="current_year"), "Job title")

    # Both titles are non-empty; only one names a family.
    assert (coverage["populated"], coverage["total"]) == (1, 2)
    assert coverage["complete"] is False


def test_location_chart_excludes_rows_whose_location_is_a_work_mode(db: Session) -> None:
    """544 of 1,105 production locations are `Remote, Remote, USA` or similar.
    Charting them as places would answer a different question."""
    _opp(db, "a", location="Dallas, TX")
    _opp(db, "b", location="Remote, Remote, USA")
    _opp(db, "c", location="unknown")
    db.commit()

    payload = charts.get_chart("location_by_work_mode")

    assert [point["label"] for point in payload["series"]] == ["Dallas, TX"]
    assert payload["provenance"]["row_count"] == 1


def test_location_coverage_reports_the_normalized_share_not_the_populated_one(db: Session) -> None:
    """The heart of W11. Three rows have a location; one is a place. A caption
    saying 100% would be true of the column and false about the picture."""
    _opp(db, "a", location="Dallas, TX")
    _opp(db, "b", location="Remote, Remote, USA")
    _opp(db, "c", location="onsite")
    db.commit()

    coverage = _coverage(charts.get_chart("location_by_work_mode"), "Location")

    assert (coverage["populated"], coverage["total"], coverage["percent"]) == (1, 3, 33.3)
    assert coverage["complete"] is False
    assert "after normalization" in coverage["note"]


def test_two_spellings_of_one_city_are_one_bar(db: Session) -> None:
    _opp(db, "a", location="Dallas, Texas, USA")
    _opp(db, "b", location="Dallas, TX")
    db.commit()

    series = charts.get_chart("location_by_work_mode")["series"]

    assert len(series) == 1
    assert series[0]["label"] == "Dallas, TX"
    assert series[0]["value"] == 2


def test_the_chart_can_be_scoped_to_one_work_mode(db: Session) -> None:
    """Q14: which location is most popular for onsite roles."""
    _opp(db, "a", location="Dallas, TX", work_mode="Onsite")
    _opp(db, "b", location="Plano, TX", work_mode="Remote")
    db.commit()

    payload = charts.get_chart("location_by_work_mode", work_mode="Onsite")

    assert [point["label"] for point in payload["series"]] == ["Dallas, TX"]
    assert "Onsite" in payload["title"]


def test_scoping_uses_the_derived_work_mode_too(db: Session) -> None:
    """A row with no work mode but "remote" in its location still counts as
    remote - that is the 133-row recovery, applied to a chart."""
    _opp(db, "a", location="Dallas, TX", work_mode="")
    _opp(db, "b", location="Plano, TX", work_mode="remote")
    db.commit()
    # Give the first row a derivable mode via a second field-free row.
    _opp(db, "c", location="remote", work_mode="")
    db.commit()

    payload = charts.get_chart("location_by_work_mode", work_mode="Remote")

    # "Plano, TX" has a recorded Remote; the bare "remote" row is not a place.
    assert [point["label"] for point in payload["series"]] == ["Plano, TX"]


def test_work_mode_coverage_includes_the_derived_rows(db: Session) -> None:
    _opp(db, "a", location="Dallas, TX", work_mode="Onsite")
    _opp(db, "b", location="remote", work_mode="")
    _opp(db, "c", location="Plano, TX", work_mode="")
    db.commit()

    coverage = _coverage(charts.get_chart("location_by_work_mode"), "Work mode")

    # Two of three resolve: one recorded, one derived from the location.
    assert (coverage["populated"], coverage["total"]) == (2, 3)


def test_the_assumptions_name_every_exclusion(db: Session) -> None:
    _opp(db, "a", location="Dallas, TX")
    db.commit()

    assumptions = charts.get_chart("location_by_work_mode")["provenance"]["assumptions"]

    assert any("work mode rather than a place" in text for text in assumptions)
    assert any("counted as one place" in text for text in assumptions)
