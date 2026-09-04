"""W8 — filtered search, over normalized values rather than literal ones."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, RecruiterOpportunity
from app.services import opportunity_search
from app.services.opportunity_search import SearchFilters

OWNER_ID = "owner-search"
NOW = datetime.now(UTC)


def _opp(db: Session, tag: str, **overrides) -> None:
    values = {
        "owner_id": OWNER_ID, "recruiter_number_id": 1, "gmail_message_id": f"m-{tag}",
        "job_title": "Java Developer", "status": "New", "received_at": NOW,
        "location": "", "work_mode": "", "domain": "", "extracted_skills": "Java, Spring",
    }
    values.update(overrides)
    db.add(RecruiterOpportunity(**values))


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _search(db: Session, **kwargs) -> dict:
    return opportunity_search.search(db, owner_id=OWNER_ID, filters=SearchFilters(**kwargs))


def test_one_city_two_spellings_one_result_set(db: Session) -> None:
    """The point of W10: "Dallas, Texas, USA" and "Dallas, TX" are 33 production
    rows that a literal filter splits in two."""
    _opp(db, "a", location="Dallas, Texas, USA")
    _opp(db, "b", location="Dallas, TX")
    _opp(db, "c", location="Plano, TX")
    db.commit()

    assert _search(db, location="Dallas")["count"] == 2


def test_a_two_letter_state_matches_every_city_in_it(db: Session) -> None:
    _opp(db, "a", location="Dallas, TX")
    _opp(db, "b", location="Plano, Texas, USA")
    _opp(db, "c", location="Phoenix, AZ")
    db.commit()

    assert _search(db, location="TX")["count"] == 2


def test_a_row_whose_location_is_a_work_mode_never_matches_a_place(db: Session) -> None:
    """544 of 1,105 locations are "Remote, Remote, USA" or similar. Returning
    them for a city search would be answering a different question."""
    _opp(db, "a", location="Remote, Remote, USA")
    _opp(db, "b", location="unknown")
    _opp(db, "c", location="Dallas, TX")
    db.commit()

    assert _search(db, location="Dallas")["count"] == 1


def test_work_mode_matches_a_row_that_only_says_so_in_its_location(db: Session) -> None:
    _opp(db, "a", work_mode="Remote", location="Dallas, TX")
    _opp(db, "b", work_mode="", location="Remote, Remote, USA")
    _opp(db, "c", work_mode="Onsite", location="Plano, TX")
    db.commit()

    result = _search(db, work_mode="Remote")

    assert result["count"] == 2
    assert "read from the location field" in result["derived_note"]


def test_a_derived_match_is_labelled_as_derived(db: Session) -> None:
    """A value this code worked out is a tier-2 resolution. It must not read
    like something the recruiter wrote."""
    _opp(db, "a", work_mode="", location="remote")
    _opp(db, "b", work_mode="Remote", location="Dallas, TX")
    db.commit()

    rows = {row["work_mode_source"] for row in _search(db, work_mode="Remote")["opportunities"]}

    assert rows == {"location", "recorded"}


def test_no_derived_note_when_every_match_was_recorded(db: Session) -> None:
    """A note that fires on every result is one the reader learns to skip."""
    _opp(db, "a", work_mode="Remote", location="Dallas, TX")
    db.commit()

    assert "derived_note" not in _search(db, work_mode="Remote")


def test_banking_finds_every_spelling_of_banking(db: Session) -> None:
    _opp(db, "a", domain="banking")
    _opp(db, "b", domain="Banking or Financial Services")
    _opp(db, "c", domain="Trading and Banking")
    _opp(db, "d", domain="Healthcare")
    db.commit()

    assert _search(db, domain="banking")["count"] == 3
    assert _search(db, domain="healthcare")["count"] == 1


def test_a_two_domain_value_is_found_by_either_filter(db: Session) -> None:
    _opp(db, "a", domain="Financial services/payments")
    db.commit()

    assert _search(db, domain="banking")["count"] == 1
    assert _search(db, domain="payments")["count"] == 1


def test_filters_combine(db: Session) -> None:
    _opp(db, "a", work_mode="Remote", location="Dallas, TX", domain="banking")
    _opp(db, "b", work_mode="Remote", location="Phoenix, AZ", domain="banking")
    _opp(db, "c", work_mode="Onsite", location="Dallas, TX", domain="banking")
    db.commit()

    assert _search(db, work_mode="Remote", location="Dallas", domain="banking")["count"] == 1


def test_recency_filter_bounds_the_window(db: Session) -> None:
    _opp(db, "a", received_at=NOW - timedelta(days=3))
    _opp(db, "b", received_at=NOW - timedelta(days=90))
    db.commit()

    assert _search(db, days=30)["count"] == 1


def test_query_matches_title_or_skills(db: Session) -> None:
    _opp(db, "a", job_title="Java Developer", extracted_skills="Spring")
    _opp(db, "b", job_title="Python Engineer", extracted_skills="Django, Java")
    _opp(db, "c", job_title="QA Analyst", extracted_skills="Selenium")
    db.commit()

    assert _search(db, query="Java")["count"] == 2


def test_an_unknown_work_mode_is_refused_with_the_valid_set(db: Session) -> None:
    """Refused rather than silently returning everything - an empty filter and a
    misunderstood one must not look the same."""
    result = _search(db, work_mode="Anywhere")

    assert "error" in result
    assert result["work_modes"] == ["Remote", "Onsite", "Hybrid"]


def test_results_carry_the_raw_value_beside_the_normalized_one(db: Session) -> None:
    """Nothing is rewritten in the database, and the answer can show what the
    recruiter actually sent."""
    _opp(db, "a", location="Dallas, Texas, USA")
    db.commit()

    row = _search(db, location="Dallas")["opportunities"][0]

    assert row["location"] == "Dallas, TX"
    assert row["location_raw"] == "Dallas, Texas, USA"


def test_the_filters_used_are_returned_with_the_results(db: Session) -> None:
    _opp(db, "a", work_mode="Remote")
    db.commit()

    assert _search(db, work_mode="Remote", days=30)["filters"] == {
        "work_mode": "Remote", "days": "30",
    }
