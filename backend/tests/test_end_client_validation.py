"""Every invalid string here was read out of production on 2026-09-03.

The audit found 24 of 76 populated `recruiter_opportunities.end_client` values
invalid - 21 of 29 Nvoids rows and 3 of 47 Gmail rows. These are the actual
values, not constructed examples, so a regression reproduces the real defect
rather than a plausible-looking one.
"""

from __future__ import annotations

import pytest

from app.services.end_client_validation import (
    clean_end_client,
    is_valid_end_client,
    rejection_reason,
)


# Real values from recruiter_opportunities.end_client, with the rule that must
# fire. `phone_intelligence_workflow_service` mapped ExternalOpportunity.company
# into end_client; that company came from a `client|company` label regex which
# matches the "client" inside "client-facing" and "client-side".
PRODUCTION_INVALID = [
    ("facing", "compound_tail_fragment"),
    ("facing skills.", "compound_tail_fragment"),
    ("facing experience", "compound_tail_fragment"),
    ("facing consulting skills.", "compound_tail_fragment"),
    ("facing roles and solution discussions", "compound_tail_fragment"),
    ("facing UIs with backend components.", "compound_tail_fragment"),
    ("side Technologies HTML", "compound_tail_fragment"),
    ("side patterns: safe auth flows", "label_or_clause"),
    ("facing skills<br /><br />Preferred:<br /><br />Experience working with "
     "large enterprise applications", "html_markup"),
    ("State of WI<br /><br />Description:   <br /><br />Program Related:", "html_markup"),
    ("Techspace IT<br /><br />Website<br />: <br />www.techspaceit.com", "html_markup"),
    ("c2c-requirements+unsubscribe@googlegroups.com.", "email_address"),
    ("Local to NC Only - NO", "eligibility_constraint"),
    ("Local to Idaho Only - No", "eligibility_constraint"),
    ("Server applications using", "trailing_stopword"),
    ("a leader in its industry. If you are interested in pursuing this opportunity",
     "prose_marker"),
    # A capitalised sentence is still a sentence - the rule that catches it is
    # its function words, not its length, so a longer real name stays safe.
    ("Excellent communication and client facing skills are required for this role",
     "prose_marker"),
]

# Real values that must survive untouched - from recruiter_emails.end_client,
# which the audit measured at 0% suspect across 225 rows.
PRODUCTION_VALID = [
    "Capgemini",
    "Morgan Stanley",
    "JPMC",
    "JPMorgan Chase & Co.",
    "TCS",
    "Deloitte",
    "Mphasis",
    "American Airlines",
    "Delta Airlines",
    "AT&T",
    "Wells Fargo",
    "Ally Bank",
    "Merrill Lynch",
    "American Express",
    "Ford Motor Company",
    "Citi",
    "Adobe",
    "Equifax",
    "Vanguard",
    "DTCC",
    "Plymouth Rock",
    "Caliber Home Loans",
    "ED Jones",
    "NC DHHS",
    "State of NC",
    "State of NC DHHS",
    "State of Virginia",
    "State of NY ITS",
    "State of South Carolina (SCDOR)",
    "U.S. Securities and Exchange Commission (SEC)",
    "Sligo Software Solutions - IES - NYS",
    # 85 characters, 13 tokens - the longest legitimate value in production, and
    # the one an 80-character cap destroyed.
    "State of OR (OHA/ODHS - Oregon Health Authority/ Oregon Department of Human Services)",
    "Amdocs/ Charter Communication",
    "Tech M/AT&T",
]


@pytest.mark.parametrize("value,expected_reason", PRODUCTION_INVALID)
def test_production_invalid_values_are_blanked(value: str, expected_reason: str) -> None:
    assert clean_end_client(value) == ""
    assert not is_valid_end_client(value)
    assert rejection_reason(value) == expected_reason


@pytest.mark.parametrize("value", PRODUCTION_VALID)
def test_production_valid_values_survive_unchanged(value: str) -> None:
    assert clean_end_client(value) == value
    assert is_valid_end_client(value)
    assert rejection_reason(value) == ""


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t"])
def test_absence_stays_absence(value: str | None) -> None:
    """Blank in, blank out. Never a substituted value, never a guess."""
    assert clean_end_client(value) == ""


def test_cleaning_is_idempotent() -> None:
    """The remediation script may be re-run; a second pass must change nothing."""
    for value, _ in PRODUCTION_INVALID:
        once = clean_end_client(value)
        assert clean_end_client(once) == once
    for value in PRODUCTION_VALID:
        once = clean_end_client(value)
        assert clean_end_client(once) == once


def test_no_partial_repair() -> None:
    """A fragment is not a damaged name. Trimming markup off one would produce a
    value that passes every check and is still about the wrong subject."""
    assert clean_end_client("facing skills<br />Preferred:") == ""
    assert clean_end_client("side Technologies HTML") == ""


def test_surrounding_whitespace_is_not_a_rejection() -> None:
    assert clean_end_client("  Capgemini  ") == "Capgemini"


def test_length_boundary() -> None:
    assert clean_end_client("A" * 100) == "A" * 100
    assert clean_end_client("A" * 101) == ""
    assert rejection_reason("A" * 101) == "too_long_101_chars"


def test_lowercase_start_is_rejected_but_ebay_shape_is_kept() -> None:
    assert clean_end_client("a leader in industry") == ""
    assert rejection_reason("a leader in industry") == "uncapitalised"
    assert clean_end_client("eBay") == "eBay"
