"""§16.9 acceptance tests — searching what is already stored.

Verified against production on 2026-09-04: Morgan Stanley 24 hits (14 recorded
in a field, 10 described only), Deloitte 85 (8 end client, 18 partner, 59
described), Citi 15 with **510 candidates rejected** by word boundary - the
`citizenship` problem, caught.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import Base, RecruiterEmail, RecruiterOpportunity
from app.services import company_mentions as CM
from app.services import end_client_search

OWNER_ID = "owner-ecs"
NOW = datetime.now(UTC)


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _email(db: Session, tag: str, **overrides) -> None:
    values = {
        "owner_id": OWNER_ID, "sender": "r@agency.com", "subject": "Java role",
        "body": "", "role": "Java Developer", "external_message_id": f"m-{tag}",
        "end_client": "", "implementation_partner": "", "company": "Acme Staffing",
    }
    values.update(overrides)
    db.add(RecruiterEmail(**values))


def _opportunity(db: Session, tag: str, **overrides) -> None:
    values = {
        "owner_id": OWNER_ID, "recruiter_number_id": 1, "gmail_message_id": f"o-{tag}",
        "job_title": "Java Developer", "status": "New", "end_client": "",
        "implementation_partner": "", "received_at": NOW,
    }
    values.update(overrides)
    db.add(RecruiterOpportunity(**values))


def _nvoids(db: Session, tag: str, **overrides) -> None:
    values = {
        "owner_id": OWNER_ID, "feed_source_id": 1, "source_type": "nvoids",
        "external_post_id": f"x-{tag}", "role": "Java Developer",
        "raw_body": "", "company": "", "dedupe_hash": f"h-{tag}",
    }
    values.update(overrides)
    db.add(ExternalOpportunity(**values))


def search(db: Session, company: str, **kwargs) -> dict:
    return end_client_search.search(db, company, owner_id=OWNER_ID, **kwargs)


def test_citizenship_is_rejected_and_the_rejection_is_reported(db: Session) -> None:
    """§16.9 #1 end to end. The broad SQL filter is allowed to over-return
    precisely because the word-bounded pass follows it - and the count of what
    it threw away is reported rather than hidden."""
    for index in range(3):
        _email(db, f"c{index}", body="US citizenship required for this position")
    _email(db, "real", end_client="Citi")
    db.commit()

    result = search(db, "Citi")

    assert result["count"] == 1
    assert result["rejected_by_word_boundary"] == 3
    assert "citizenship" in result["rejection_note"]


def test_the_three_populations_are_counted_separately(db: Session) -> None:
    """§16.9 #5-#7. Recorded in a field, and merely named in a description, are
    different kinds of fact. An answer that blends them is wrong even when every
    row is right."""
    _email(db, "field", end_client="Morgan Stanley")
    _email(db, "body", body="Prior Morgan Stanley experience preferred")
    _nvoids(db, "ext", raw_body="Client is Morgan Stanley")
    db.commit()

    result = search(db, "Morgan Stanley")

    assert result["count"] == 3
    assert result["by_evidence"][CM.MENTION_END_CLIENT] == 1
    assert result["by_evidence"][CM.MENTION_DESCRIBED] == 2
    assert "1 record(s) name Morgan Stanley in a field" in result["summary"]


def test_the_deloitte_shape_labels_partner_rows_as_partner(db: Session) -> None:
    """§16.9 #8 and #20. In production, 18 Deloitte rows have Edward Jones as the
    end client and Deloitte as the partner. Reading `end_client` alone called all
    18 errors (§16.11); they are correct rows about a different column."""
    for index in range(3):
        _email(db, f"p{index}", end_client="ED Jones", implementation_partner="Deloitte")
    _email(db, "client", end_client="Deloitte")
    db.commit()

    result = search(db, "Deloitte")

    assert result["by_evidence"][CM.MENTION_PARTNER_FIELD] == 3
    assert result["by_evidence"][CM.MENTION_END_CLIENT] == 1
    partner_rows = [r for r in result["results"] if r["evidence"] == CM.MENTION_PARTNER_FIELD]
    assert all(row["phrasing"] == "recorded as the implementation partner" for row in partner_rows)


def test_recorded_evidence_sorts_above_a_bare_mention(db: Session) -> None:
    """The rows that support a claim must not be buried under the ones that
    merely name the company."""
    for index in range(4):
        _email(db, f"d{index}", body="Similar to Capgemini engagements")
    _email(db, "field", end_client="Capgemini")
    db.commit()

    results = search(db, "Capgemini")["results"]

    assert results[0]["evidence"] == CM.MENTION_END_CLIENT


def test_a_described_row_never_claims_a_relationship(db: Session) -> None:
    """§16.9 #6."""
    _nvoids(db, "a", raw_body="Experience with Cognizant is a plus")
    db.commit()

    row = search(db, "Cognizant")["results"][0]

    assert row["is_relationship_claim"] is False
    assert row["phrasing"] == "named in the description"


def test_nothing_found_is_not_reported_as_a_negative_finding(db: Session) -> None:
    """"No requirements from X" is a claim about the world. "Nothing stored names
    X" is the truth."""
    _email(db, "a", end_client="Capgemini")
    db.commit()

    result = search(db, "Vanguard")

    assert result["count"] == 0
    assert "not evidence that no such requirement exists" in result["nothing_found_note"]


def test_the_search_token_is_the_most_selective_word(db: Session) -> None:
    """`Morgan Stanley` filters on `stanley`, not the `morgan` that dragged in a
    posting located in Morgan, Utah."""
    assert end_client_search._search_terms("Morgan Stanley") == ["stanley"]
    assert end_client_search._search_terms("Citi") == ["citi"]


def test_a_location_named_morgan_is_not_a_morgan_stanley_hit(db: Session) -> None:
    """§16.9 #2, at the search level."""
    _nvoids(db, "utah", raw_body="Onsite role in Morgan, Utah, USA")
    db.commit()

    assert search(db, "Morgan Stanley")["count"] == 0


def test_an_empty_company_is_refused_rather_than_matching_everything(db: Session) -> None:
    assert "error" in search(db, "")
    assert "error" in search(db, "   ")


def test_coverage_travels_with_the_result(db: Session) -> None:
    """W12's contract still applies: a caller must be able to see how sparse the
    fields behind the answer are."""
    _email(db, "a", end_client="Capgemini")
    db.commit()

    result = search(db, "Capgemini")

    assert "end_client" in result["field_coverage"]
    assert result["population"] == "already stored in the application"
