"""W10 — the vocabularies, checked against the values production actually holds.

Every string below was read out of `recruiter_opportunities` on 2026-09-04.
"""

from __future__ import annotations

import pytest

from app.services import normalization as N


# --- Work mode ------------------------------------------------------------


@pytest.mark.parametrize("stored,expected", [
    ("Remote", N.REMOTE), ("remote", N.REMOTE), ("Onsite", N.ONSITE), ("ONSITE", N.ONSITE),
    ("Hybrid", N.HYBRID),
    # The three stragglers: Hybrid spelled long. "Hybrid - 3 Days onsite"
    # contains both words and the leading one is the answer.
    ("Hybrid - 3 Days onsite", N.HYBRID), ("Hybrid (3 days onsite)", N.HYBRID),
    ("Work from home", N.REMOTE),
])
def test_work_mode_collapses_to_three(stored: str, expected: str) -> None:
    assert N.normalize_work_mode(stored) == expected


@pytest.mark.parametrize("stored", ["", None, "Unknown", "Plano, Texas, USA", "n/a"])
def test_a_non_work_mode_is_not_forced_into_one(stored: str | None) -> None:
    assert N.normalize_work_mode(stored) is None


def test_work_mode_is_read_from_location_when_its_own_column_is_empty() -> None:
    """484 rows hold a work mode in `location`; on 133 `work_mode` is empty.
    Recovering those lifts coverage from 66.3% to 78.2% - without writing."""
    derived = N.derive_work_mode("", "Remote, Remote, USA")

    assert derived.value == N.REMOTE
    assert derived.source == "location"
    assert derived.is_derived


def test_a_recorded_work_mode_is_never_overridden_by_the_location() -> None:
    """Only 4 rows disagree when both are populated. The column that was
    written for the purpose wins."""
    derived = N.derive_work_mode("Onsite", "remote")

    assert derived.value == N.ONSITE
    assert derived.source == "recorded"
    assert not derived.is_derived


def test_no_work_mode_anywhere_reports_none_rather_than_guessing() -> None:
    derived = N.derive_work_mode("", "Plano, Texas, USA")

    assert derived.value is None
    assert derived.source == "none"


# --- Location -------------------------------------------------------------


@pytest.mark.parametrize("stored,expected", [
    ("Dallas, Texas, USA", "Dallas, TX"),
    ("Dallas, TX", "Dallas, TX"),
    ("Plano, Texas, USA", "Plano, TX"),
    ("Phoenix, AZ", "Phoenix, AZ"),
    ("NYC, NY", "New York, NY"),
    ("Saint Louis, MO", "Saint Louis, MO"),
    ("Phoenix", "Phoenix"),
])
def test_two_spellings_of_one_place_become_one(stored: str, expected: str) -> None:
    """"Dallas, Texas, USA" (19 rows) and "Dallas, TX" (14) are 33 rows a
    literal filter splits in two."""
    place = N.normalize_location(stored)

    assert place is not None
    assert place.as_text() == expected


@pytest.mark.parametrize("stored", [
    "Remote, Remote, USA", "remote", "onsite", "hybrid", "unknown", "USA", "", None,
])
def test_a_work_mode_in_the_location_column_is_not_a_place(stored: str | None) -> None:
    """544 of 1,105 populated locations are this. None is the honest answer -
    it is not a location that happens to be missing, it is the wrong kind of
    value in the column."""
    assert N.normalize_location(stored) is None


# --- Domain ---------------------------------------------------------------


@pytest.mark.parametrize("stored", [
    "banking", "Banking", "financial services", "Banking/Financial Services",
    "Banking or Financial Services", "Trading and Banking", "Banking and Finance",
    "Banking / Financial Services / Capital Markets", "Finance", "Mortgage",
    "Capital Markets, Trading, Wealth Management, Financial Services, Banking",
])
def test_every_banking_spelling_reaches_one_concept(stored: str) -> None:
    """Exact match on "banking" returns 9 rows. The vocabulary returns 40."""
    assert "banking_finance" in N.normalize_domain(stored)


def test_a_value_naming_two_domains_returns_both() -> None:
    """A one-to-one map would have to pick one and lose the row from whichever
    filter did not win."""
    assert set(N.normalize_domain("Financial services/payments")) == {"banking_finance", "payments"}
    assert set(N.normalize_domain("Cards & Payments / Asset & Wealth Management")) == {
        "payments", "banking_finance",
    }
    assert set(N.normalize_domain("Government/Regulatory")) == {"government"}


@pytest.mark.parametrize("stored,expected", [
    ("Healthcare", "healthcare"), ("pharma", "healthcare"), ("Airline", "airline"),
    ("telecommunications", "telecom"), ("Insurance", "insurance"),
    ("retail", "retail"), ("transportation", "transportation"), ("government", "government"),
])
def test_the_remaining_concepts(stored: str, expected: str) -> None:
    assert expected in N.normalize_domain(stored)


def test_an_unrecognised_domain_returns_nothing_rather_than_a_guess() -> None:
    assert N.normalize_domain("Widgets") == ()
    assert N.normalize_domain("Unknown") == ()


def test_a_user_may_use_their_own_word_for_a_domain() -> None:
    """"banking" and "finance" both reach banking_finance without the user
    learning the vocabulary's spelling."""
    assert N.domain_matches("Trading and Banking", "banking")
    assert N.domain_matches("Trading and Banking", "finance")
    assert N.domain_matches("Trading and Banking", "banking_finance")
    assert not N.domain_matches("Trading and Banking", "healthcare")


# --- Interview type -------------------------------------------------------


@pytest.mark.parametrize("stored", [
    "F2F Interview", "F2F", "F2F interview", "In Person Interview",
    "In-person interview", "Face to Face Interview", "F2F final round",
])
def test_six_spellings_of_face_to_face_are_one_concept(stored: str) -> None:
    assert N.IN_PERSON in N.normalize_interview_type(stored)


def test_an_interview_process_with_two_formats_names_both() -> None:
    assert set(N.normalize_interview_type("2 Video + F2F")) == {N.VIDEO, N.IN_PERSON}
    assert N.VIDEO in N.normalize_interview_type("Virtual")
    assert N.PHONE in N.normalize_interview_type("Telephonic screen")
