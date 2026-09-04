"""§16.9 acceptance tests #9-#12 — building the nvoids search string."""

from __future__ import annotations

import pytest

from app.external_feeds.service import (
    QUERY_MODE_COMPOSED,
    QUERY_MODE_END_CLIENT_ONLY,
    ExternalFeedService,
    build_end_client_clause,
)

DEFAULT = "java and spring* not(*js)"


class _Feed:
    default_query = DEFAULT


def build(**kwargs) -> str:
    return ExternalFeedService.build_nvoids_query(_Feed(), **kwargs)


def test_composed_joins_every_criterion_given() -> None:
    """§16.9 #9."""
    assert build(
        job_role="java", search_location="tx or texas", end_client="Morgan Stanley"
    ) == "(tx or texas) and java and morgan and stanley"


def test_omitted_criteria_are_omitted_not_defaulted() -> None:
    """§16.9 #9. A blank location must not become a placeholder clause."""
    assert build(job_role="java", end_client="Citi") == "java and citi"
    assert build(search_location="New Jersey", end_client="Citi") == "(New Jersey) and citi"


def test_end_client_only_drops_role_and_location() -> None:
    """§16.9 #10. Composition collapses to ~2 rows, so discovery needs a mode
    that asks for the company alone."""
    assert build(
        job_role="java",
        search_location="tx or texas",
        end_client="Morgan Stanley",
        query_mode=QUERY_MODE_END_CLIENT_ONLY,
    ) == "morgan and stanley"


def test_end_client_only_without_a_client_falls_back_rather_than_matching_everything() -> None:
    """An empty mode must never silently search for the whole board."""
    assert build(job_role="java", query_mode=QUERY_MODE_END_CLIENT_ONLY) == "java"
    assert build(query_mode=QUERY_MODE_END_CLIENT_ONLY) == DEFAULT


def test_custom_query_still_overrides_both_modes() -> None:
    """§16.9 #11. Someone who typed raw nvoids syntax meant it."""
    for mode in (QUERY_MODE_COMPOSED, QUERY_MODE_END_CLIENT_ONLY):
        assert build(
            job_role="java", end_client="Citi", custom_query="(tx) and spark", query_mode=mode
        ) == "(tx) and spark"


def test_a_two_word_company_never_degrades_to_its_first_token() -> None:
    """§16.9 #12. The bare `morgan` is what returned a posting located in
    Morgan, Utah."""
    clause = build(end_client="Morgan Stanley", query_mode=QUERY_MODE_END_CLIENT_ONLY)

    assert clause == "morgan and stanley"
    assert clause != "morgan"


@pytest.mark.parametrize("raw,expected", [
    ("Morgan Stanley", "morgan and stanley"),
    ("Citi", "citi"),
    ("  Wells   Fargo  ", "wells and fargo"),
    ("AT&T", "at&t"),
    ("J.P. Morgan Chase", "j and p and morgan and chase"),
    ("", ""),
    (None, ""),
])
def test_the_client_clause_is_a_conjunction_of_its_own_words(raw, expected) -> None:
    assert build_end_client_clause(raw) == expected


def test_nothing_set_returns_the_configured_default() -> None:
    assert build() == DEFAULT
