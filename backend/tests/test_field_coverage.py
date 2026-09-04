"""W12 — the answering rule, enforced by the tool rather than trusted to the model.

The classification numbers here mirror production on 2026-09-04: 1,117
opportunities, `end_client` on 49, `implementation_partner` on 18, `prime_vendor`
and `employment_type` on none.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base, RecruiterOpportunity
from app.services import field_coverage

OWNER_ID = "owner-coverage"


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        # 10 rows: 2 name an end client, none name a prime vendor. The shape of
        # production in miniature - a field that answers a little, and one that
        # cannot answer at all.
        for index in range(10):
            session.add(
                RecruiterOpportunity(
                    owner_id=OWNER_ID,
                    recruiter_number_id=1,
                    gmail_message_id=f"msg-{index}",
                    job_title=f"Java Developer {index}",
                    end_client="Capgemini" if index < 2 else "",
                    work_mode="Remote" if index < 7 else "",
                    prime_vendor="",
                    employment_type="",
                    status="New",
                )
            )
        session.commit()
        yield session


def test_coverage_is_corpus_wide_not_subset(db: Session) -> None:
    result = field_coverage.coverage(db, "end_client", owner_id=OWNER_ID)
    assert (result.populated, result.total, result.percent) == (2, 10, 20.0)
    assert result.as_dict()["scope"] == "corpus"


def test_subset_coverage_is_labelled_and_cannot_pass_as_corpus(db: Session) -> None:
    """The two rows that *do* name a client are 100% populated among themselves.

    That figure is true and useless as a reliability signal, which is why it
    carries `scope: subset` and a note pointing back at the corpus number.
    """
    rows = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.end_client != "").all()
    subset = field_coverage.subset_coverage(rows, "end_client")
    assert subset["percent"] == 100.0
    assert subset["scope"] == "subset"
    assert "corpus" in subset["note"]
    assert field_coverage.coverage(db, "end_client", owner_id=OWNER_ID).percent == 20.0


def test_zero_coverage_fields_return_a_refusal(db: Session) -> None:
    for name in ("prime_vendor", "employment_type"):
        result = field_coverage.unavailable_result(name)
        assert result is not None
        assert result["unavailable"] is True
        assert result["may_answer_from_this_field"] is False
        assert "Decline" in result["instruction"]


def test_refusal_describes_the_system_not_the_world(db: Session) -> None:
    """An empty column says nothing about whether prime vendors exist."""
    reason = field_coverage.unavailable_result("prime_vendor")["reason"]
    assert "nothing has ever written to it" in reason
    assert "does not mean there are no prime vendors" in reason


def test_usable_fields_have_no_refusal(db: Session) -> None:
    for name in ("job_title", "work_mode", "end_client", "location"):
        assert field_coverage.unavailable_result(name) is None
        assert field_coverage.availability(name) != field_coverage.UNAVAILABLE


def test_sparse_but_populated_field_is_restricted_not_withheld(db: Session) -> None:
    """`implementation_partner` has real values on 18 production rows.

    A lookup on a named record is legitimate; a claim about which partners are
    active is not. So it is classified UNAVAILABLE for population claims while
    its per-row value still travels in the payload.
    """
    assert field_coverage.availability("implementation_partner") == field_coverage.UNAVAILABLE
    assert field_coverage.unavailable_result("implementation_partner") is not None


def test_unconfirmed_aliases_are_reported_separately_never_summed(db: Session) -> None:
    db.add_all(
        [
            RecruiterOpportunity(
                owner_id=OWNER_ID, recruiter_number_id=1, job_title="A",
                gmail_message_id="msg-A",
                end_client="American Express", status="New",
            ),
            RecruiterOpportunity(
                owner_id=OWNER_ID, recruiter_number_id=1, job_title="B",
                gmail_message_id="msg-B",
                end_client="American Express", status="New",
            ),
            RecruiterOpportunity(
                owner_id=OWNER_ID, recruiter_number_id=1, job_title="C",
                gmail_message_id="msg-C",
                end_client="AMEX", status="New",
            ),
        ]
    )
    db.commit()
    note = field_coverage.unconfirmed_alias_note(db, "end_client", owner_id=OWNER_ID)
    assert note is not None
    counts = note["groups"][0]["values"]
    assert counts == {"American Express": 2, "AMEX": 1}
    assert note["groups"][0]["status"] == "unconfirmed"
    assert note["field"] == "end_client"
    assert "Do not sum them." in note["instruction"]


def test_no_alias_note_when_only_one_spelling_is_present(db: Session) -> None:
    """A warning that fires on a single spelling is noise the model learns to skip."""
    db.add(
        RecruiterOpportunity(
            owner_id=OWNER_ID, recruiter_number_id=1, job_title="A",
                gmail_message_id="msg-A",
            end_client="American Express", status="New",
        )
    )
    db.commit()
    assert field_coverage.unconfirmed_alias_note(db, "end_client", owner_id=OWNER_ID) is None


def test_coverage_is_scoped_to_the_owner(db: Session) -> None:
    db.add(
        RecruiterOpportunity(
            owner_id="someone-else", recruiter_number_id=1, job_title="X",
                gmail_message_id="msg-X",
            end_client="Morgan Stanley", status="New",
        )
    )
    db.commit()
    assert field_coverage.coverage(db, "end_client", owner_id=OWNER_ID).total == 10


def test_empty_table_does_not_divide_by_zero(db: Session) -> None:
    result = field_coverage.coverage(db, "end_client", owner_id="nobody")
    assert (result.total, result.percent, result.may_carry_a_claim) == (0, 0.0, False)


def test_system_prompt_carries_the_policy() -> None:
    """Both controls ship together: the payload carries evidence, the prompt
    carries the rule for reading it. Either alone is weaker."""
    from app.ai.chat.system_prompt import build_system_prompt

    prompt = build_system_prompt()
    assert "field_coverage" in prompt
    assert "corpus-wide" in prompt
    assert "unavailable_fields" in prompt
    assert "unconfirmed_aliases" in prompt
    assert "never on a lookup of one record" in prompt


# --- Aggregates -----------------------------------------------------------


def test_aggregate_refusal_blocks_a_trend_over_an_unusable_field(db: Session) -> None:
    """A chart is a stronger claim than a list.

    Four rows look like four rows; a trend line over four rows looks like a
    trend. So an aggregate over a field that cannot carry a claim is not drawn
    at all - the same reasoning `provenance` already applies to a chart with
    missing provenance, where the control is "renders nothing" rather than
    "renders with a warning".
    """
    refusal = field_coverage.aggregate_refusal(["prime_vendor"], subject="the vendor chart")
    assert refusal is not None
    assert refusal["unavailable"] is True
    assert refusal["may_answer_from_this_field"] is False
    assert refusal["blocked_fields"][0]["field"] == "prime_vendor"
    assert "Do not present the vendor chart as a trend" in refusal["instruction"]
    assert "do not silently swap in another field" in refusal["instruction"]


def test_aggregate_over_usable_fields_is_not_refused(db: Session) -> None:
    assert field_coverage.aggregate_refusal(["work_mode", "job_title"], subject="x") is None
    assert field_coverage.aggregate_refusal([], subject="x") is None


def test_aggregate_refusal_names_every_blocked_field(db: Session) -> None:
    refusal = field_coverage.aggregate_refusal(
        ["prime_vendor", "employment_type", "job_title"], subject="a mixed chart"
    )
    blocked = {entry["field"] for entry in refusal["blocked_fields"]}
    assert blocked == {"prime_vendor", "employment_type"}


def test_column_coverage_is_corpus_wide_for_non_opportunity_models(db: Session) -> None:
    result = field_coverage.column_coverage(
        db, RecruiterOpportunity, "end_client", owner_id=OWNER_ID, label="End client"
    )
    assert (result["populated"], result["total"], result["percent"]) == (2, 10, 20.0)
    assert result["scope"] == "corpus"
    assert result["field"] == "recruiter_opportunities.end_client"


def test_provenance_block_carries_coverage() -> None:
    from app.mcp_server.tools import provenance

    block = provenance.block(
        metric="Approved sends",
        source="get_chart/activity_trend",
        row_count=3,
        coverage=[{"field": "recruiter_emails.state", "percent": 100.0, "scope": "corpus"}],
    )
    assert block["coverage"][0]["percent"] == 100.0
    # Absent coverage is an empty list, never a missing key - the frontend
    # validates provenance shape and a missing key would fail the render.
    assert provenance.block(metric="m", source="s", row_count=0)["coverage"] == []


# --- W14: placeholders are not values -------------------------------------


def test_placeholder_strings_count_as_missing(db: Session) -> None:
    """The fourth appearance of one defect, fixed in the measure.

    `premium_number_contacts.owner_name` reads 100% populated and is the literal
    "Unknown" on 434 of 497 live rows. Counting non-empty strings overstated four
    fields in that table alone.
    """
    for value in ("Unknown", "unknown", "  N/A  ", "not specified", "TBD", "-", ""):
        assert field_coverage.is_placeholder(value)
    for value in ("Capgemini", "Recruiter", "Unknown Systems Inc", "NA Solutions"):
        assert not field_coverage.is_placeholder(value)


def test_placeholder_is_excluded_from_corpus_coverage(db: Session) -> None:
    db.add_all([
        RecruiterOpportunity(
            owner_id=OWNER_ID, recruiter_number_id=1, gmail_message_id="p1",
            job_title="A", end_client="Unknown", status="New",
        ),
        RecruiterOpportunity(
            owner_id=OWNER_ID, recruiter_number_id=1, gmail_message_id="p2",
            job_title="B", end_client="N/A", status="New",
        ),
    ])
    db.commit()
    # Two more rows, both placeholders: the total grows, the populated count
    # does not. A field does not become better answered by being filled in with
    # the word "Unknown".
    result = field_coverage.coverage(db, "end_client", owner_id=OWNER_ID)
    assert (result.populated, result.total) == (2, 12)


def test_a_counter_of_zero_is_a_measurement_not_a_placeholder(db: Session) -> None:
    """Non-text columns keep the plain NOT NULL test - `lower(trim(...))` on an
    integer is an error, and 0 is an answer."""
    from app.models import PremiumNumberContact

    assert field_coverage.populated_filter(PremiumNumberContact.seen_count) is not None
    assert not field_coverage._is_text(PremiumNumberContact.seen_count)
    assert field_coverage._is_text(PremiumNumberContact.company)


# --- W13: contacts under the coverage contract ----------------------------


def _contact(db: Session, **overrides) -> None:
    from app.models import PremiumNumberContact

    values = {
        "owner_id": OWNER_ID, "normalized_phone_number": f"1555000{overrides.pop('n', 0)}",
        "display_phone_number": "(555) 000-0000", "recruiter_name": "Jane Recruiter",
        "company": "Acme Staffing", "designation": "Technical Recruiter",
        "is_recruiter": True, "seen_count": 1,
    }
    values.update(overrides)
    db.add(PremiumNumberContact(**values))


def test_contact_coverage_defaults_to_active_contacts(db: Session) -> None:
    """The Recycle Bin is a working queue, not an archive of the false - but a
    binned recruiter must not appear in a recommendation about who to contact
    now, so it is excluded by default."""
    from datetime import UTC, datetime

    _contact(db, n=1)
    _contact(db, n=2)
    _contact(db, n=3, deleted_at=datetime.now(UTC))
    db.commit()

    active = field_coverage.contact_coverage(db, "company", owner_id=OWNER_ID)
    assert (active.populated, active.total) == (2, 2)


def test_all_time_scope_reaches_the_recycle_bin_when_asked(db: Session) -> None:
    from datetime import UTC, datetime

    _contact(db, n=1)
    _contact(db, n=2, deleted_at=datetime.now(UTC))
    db.commit()

    history = field_coverage.contact_coverage(
        db, "company", owner_id=OWNER_ID, scope=field_coverage.SCOPE_ALL_TIME
    )
    assert history.total == 2


def test_an_unknown_designation_does_not_count_as_a_designation(db: Session) -> None:
    _contact(db, n=1, designation="Technical Recruiter")
    _contact(db, n=2, designation="Unknown")
    db.commit()

    result = field_coverage.contact_coverage(db, "designation", owner_id=OWNER_ID)
    assert (result.populated, result.total) == (1, 2)


def test_a_complete_but_uninformative_contact_field_is_refused(db: Session) -> None:
    """`recruiter_verification_level` is on every contact and 19 of 497 say
    "verified". The field is complete; the verification is not."""
    refusal = field_coverage.contact_unavailable_result("recruiter_verification_level")
    assert refusal is not None
    assert "the verification is not" in refusal["reason"]

    assert field_coverage.contact_unavailable_result("company") is None


def test_unknown_scope_is_refused_rather_than_silently_defaulted(db: Session) -> None:
    with pytest.raises(ValueError):
        field_coverage.contact_coverage(db, "company", owner_id=OWNER_ID, scope="everything")
