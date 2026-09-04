"""W9 — the judgment query, answered without a judgment.

"Which recruiter is worth keeping in touch with" has no ground truth. The rule
these tests pin is that the ordering never becomes a verdict: it states its own
definition, shows every number it used, and classifies nobody.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, EmailReplyMessage, PremiumNumberContact, RecruiterOpportunity
from app.services import field_coverage, recruiter_ranking

OWNER_ID = "owner-rank"
NOW = datetime.now(UTC)


def _contact(db: Session, n: int, **overrides) -> PremiumNumberContact:
    values = {
        "owner_id": OWNER_ID,
        "normalized_phone_number": f"155500000{n}",
        "display_phone_number": f"(555) 000-000{n}",
        "recruiter_name": f"Recruiter {n}",
        "company": "Acme Staffing",
        "designation": "Technical Recruiter",
        "recruiter_email": f"r{n}@agency.com",
        "is_recruiter": True,
        "seen_count": 1,
    }
    values.update(overrides)
    row = PremiumNumberContact(**values)
    db.add(row)
    db.flush()
    return row


def _opportunity(db: Session, contact_id: int, days_ago: int, tag: str) -> None:
    db.add(
        RecruiterOpportunity(
            owner_id=OWNER_ID,
            recruiter_number_id=contact_id,
            gmail_message_id=f"msg-{tag}",
            job_title="Java Developer",
            status="New",
            received_at=NOW - timedelta(days=days_ago),
        )
    )


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_the_rule_ships_with_the_result(db: Session) -> None:
    """§12.7: a rule the reader cannot see is a hidden score."""
    _contact(db, 1)
    db.commit()

    result = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)

    assert result["rule"] == "volume"
    assert "how many requirements they sent you" in result["rule_statement"]
    # And the alternatives, so disagreeing with the definition is a re-rank
    # rather than an argument.
    assert set(result["other_rules"]) == {"recent", "responsive", "recurring"}


def test_the_ordering_is_not_a_verdict(db: Session) -> None:
    """`limit` truncates a list. It does not classify anyone as not worth
    contacting - that would be `LIKELY_MIN` rebuilt one table over."""
    for n in range(1, 4):
        _contact(db, n)
    db.commit()

    result = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID, limit=1)

    assert result["count"] == 3
    assert len(result["recruiters"]) == 1
    assert "has been judged not worth contacting" in result["not_a_threshold"]


def test_volume_orders_by_requirements_then_recency(db: Session) -> None:
    quiet = _contact(db, 1, recruiter_name="Quiet")
    busy = _contact(db, 2, recruiter_name="Busy")
    stale = _contact(db, 3, recruiter_name="Stale")
    db.commit()
    _opportunity(db, busy.id, 5, "b1")
    _opportunity(db, busy.id, 3, "b2")
    _opportunity(db, stale.id, 200, "s1")
    _opportunity(db, quiet.id, 1, "q1")
    db.commit()

    ranked = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)["recruiters"]

    assert [row["name"] for row in ranked] == ["Busy", "Quiet", "Stale"]
    assert ranked[0]["opportunities_sent"] == 2


def test_every_number_the_ordering_used_is_on_the_row(db: Session) -> None:
    """A rank whose inputs are invisible is a score."""
    contact = _contact(db, 1, seen_count=7)
    db.commit()
    _opportunity(db, contact.id, 4, "o1")
    db.commit()

    row = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)["recruiters"][0]

    assert row["opportunities_sent"] == 1
    assert row["times_seen"] == 7
    assert row["days_since_last"] == 4
    assert row["last_opportunity"] is not None
    assert row["replies_received"] == 0


def test_replies_are_a_bonus_signal_not_a_base_one(db: Session) -> None:
    """Recorded for 19 of 497 live contacts. A reply-led default would sort 96%
    of the population arbitrarily and call it a ranking - so `volume` ignores
    replies, and `responsive` exists for when the user asks for them."""
    talker = _contact(db, 1, recruiter_name="Talker", recruiter_email="talker@agency.com")
    sender = _contact(db, 2, recruiter_name="Sender", recruiter_email="sender@agency.com")
    db.commit()
    for i in range(4):
        _opportunity(db, sender.id, i + 1, f"s{i}")
    _opportunity(db, talker.id, 1, "t1")
    db.add(EmailReplyMessage(
        owner_id=OWNER_ID, conversation_id=1, direction="inbound",
        external_message_id="reply-1", sender="Talker <talker@agency.com>",
        body="yes", snippet="yes",
    ))
    db.commit()

    by_volume = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID, rule="volume")
    by_replies = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID, rule="responsive")

    assert [r["name"] for r in by_volume["recruiters"]] == ["Sender", "Talker"]
    assert [r["name"] for r in by_replies["recruiters"]] == ["Talker", "Sender"]
    assert by_replies["recruiters"][0]["replies_received"] == 1


def test_a_recruiter_who_never_wrote_sorts_last_not_first(db: Session) -> None:
    """`days_since_last` is None for a contact with no opportunity. Treated as
    infinitely old; a null must not read as "today"."""
    never = _contact(db, 1, recruiter_name="Never")
    recent = _contact(db, 2, recruiter_name="Recent")
    db.commit()
    _opportunity(db, recent.id, 30, "r1")
    db.commit()

    ranked = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID, rule="recent")["recruiters"]

    assert [row["name"] for row in ranked] == ["Recent", "Never"]
    assert ranked[1]["days_since_last"] is None


def test_placeholders_are_blanked_not_printed(db: Session) -> None:
    """W14. "Unknown" is not a company, and printing it puts a fake answer in
    front of the reader."""
    _contact(db, 1, company="Unknown", designation="Unknown", recruiter_name="Real Name")
    db.commit()

    row = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)["recruiters"][0]

    assert row["company"] == ""
    assert row["designation"] == ""
    assert row["name"] == "Real Name"


def test_recycle_bin_contacts_are_excluded_by_default(db: Session) -> None:
    """A binned recruiter still posted what they posted, but must not appear in
    a recommendation about who to contact now."""
    _contact(db, 1, recruiter_name="Live")
    _contact(db, 2, recruiter_name="Binned", deleted_at=NOW)
    db.commit()

    active = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)
    history = recruiter_ranking.rank_recruiters(
        db, owner_id=OWNER_ID, scope=field_coverage.SCOPE_ALL_TIME
    )

    assert [r["name"] for r in active["recruiters"]] == ["Live"]
    assert active["population"] == "active"
    assert {r["name"] for r in history["recruiters"]} == {"Live", "Binned"}


def test_employers_are_not_ranked_as_recruiters(db: Session) -> None:
    _contact(db, 1, recruiter_name="Recruiter", is_recruiter=True)
    _contact(db, 2, recruiter_name="Employer", is_recruiter=False, is_employer=True)
    db.commit()

    ranked = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)["recruiters"]

    assert [row["name"] for row in ranked] == ["Recruiter"]


def test_an_unknown_rule_is_refused_with_the_valid_set(db: Session) -> None:
    with pytest.raises(ValueError) as error:
        recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID, rule="best")

    assert "volume" in str(error.value)


def test_empty_population_returns_the_rule_rather_than_nothing(db: Session) -> None:
    result = recruiter_ranking.rank_recruiters(db, owner_id=OWNER_ID)

    assert result["count"] == 0
    assert result["rule_statement"]
