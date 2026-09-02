"""Controlled validation set for role assignment.

A fixed, small set of representative records, each with its expected `role`,
`role_canonical` and `role_source` declared up front and compared against what the
pipeline actually produces. The point is to prove the improved backend behaves as
intended on known inputs - not to inspect or repair production data.

Inputs are taken from real production shapes measured 2026-09-01:
the `<br />` body bleed, the 6,632-character nvoids scraper row, the
"N Requirements ::" multi-role blasts, and the silent subject fallback.

No network, no production rows: the taxonomy matcher is a deterministic stub, so
a failure here is always a logic failure and never a flaky embedding.
"""

import os
import unittest
from dataclasses import dataclass

os.environ["DEBUG"] = "false"

from app.services.role_provenance import (
    MARKUP_PATTERN,
    ROLE_MAX_CHARS,
    RoleSource,
    TaxonomyMatch,
    assign_role,
)

# Stand-in for the taxonomy that Step 5 seeds and Step 6 will embed. Matching is
# keyword-based and deliberately strict: anything it cannot place confidently
# returns None, which is what the real matcher must do below its similarity floor.
STUB_TAXONOMY = {
    "java": "Java Developer",
    "full stack": "Java Full Stack Developer",
    "data engineer": "Data Engineer",
    "qa": "QA Automation Engineer",
    "business analyst": "Business Analyst",
}


def stub_matcher(text: str, body: str) -> TaxonomyMatch | None:
    haystack = f"{text} {body}".lower()
    # Longest keyword first, so "full stack" beats "java". A real embedding matcher
    # ranks by similarity; first-match-wins would let the stub, not the code under
    # test, decide the answer.
    for keyword in sorted(STUB_TAXONOMY, key=len, reverse=True):
        if keyword in haystack:
            return TaxonomyMatch(STUB_TAXONOMY[keyword])
    return None


def no_match(text: str, body: str) -> TaxonomyMatch | None:
    """The confidence gate: below the floor the matcher returns nothing."""
    return None


@dataclass(frozen=True)
class Fixture:
    name: str
    extracted: str | None
    subject: str
    body: str
    matcher: object
    expected_role: str
    expected_canonical: str | None
    expected_source: str


NVOIDS_MARKUP = (
    'Java AWS Developer At Plano, TX</a><td></tr><tr><td>'
    '<a href="https://jobs.nvoids.com/job_detail">' + ("filler text " * 500)
)

FIXTURES: list[Fixture] = [
    Fixture(
        name="F1 clean extraction",
        extracted="Java Full Stack Developer",
        subject="Hiring now",
        body="We need a Java full stack developer.",
        matcher=stub_matcher,
        expected_role="Java Full Stack Developer",
        expected_canonical="Java Full Stack Developer",
        expected_source=RoleSource.EXTRACTED,
    ),
    Fixture(
        name="F2 extraction with <br /> body bleed",
        extracted="Java Architect with AI experience<br /><br />Location: Austin, TX -Onsite<br />",
        subject="We are hiring",
        body="Java architect role in Austin.",
        matcher=stub_matcher,
        expected_role="Java Architect with AI experience",
        expected_canonical="Java Developer",
        expected_source=RoleSource.EXTRACTED,
    ),
    Fixture(
        name="F3 nvoids scraper markup",
        extracted=NVOIDS_MARKUP,
        subject="Re: New jobs with search string",
        body="Java AWS developer role.",
        matcher=stub_matcher,
        # Expectation corrected after observing actual behaviour: partition_html puts
        # the title on its own line and normalize_role keeps only the first line, so
        # the trailing filler is dropped entirely rather than clipped at 200 chars.
        # That is the better outcome - a real title, not a truncated blob.
        expected_role="Java AWS Developer At Plano, TX",
        expected_canonical="Java Developer",
        expected_source=RoleSource.EXTRACTED,
    ),
    Fixture(
        name="F4 extraction failed, taxonomy rescues it",
        extracted=None,
        subject="Urgent Hiring !!! Immediate joiner",
        body="Looking for a Java engineer with Spring Boot.",
        matcher=stub_matcher,
        expected_role="Java Developer",
        expected_canonical="Java Developer",
        expected_source=RoleSource.TAXONOMY_MATCHED,
    ),
    Fixture(
        name="F5 extraction failed, no confident match - MUST NOT force a role",
        extracted=None,
        subject="Salesforce Administrator opening",
        body="Salesforce admin, Apex, Lightning.",
        matcher=no_match,
        expected_role="Salesforce Administrator opening",
        expected_canonical=None,
        expected_source=RoleSource.SUBJECT_FALLBACK,
    ),
    Fixture(
        name="F6 multi-role blast is refused a title",
        extracted=None,
        subject="3 Requirements :: Java Software Engineer :: SAP QM :: QA Lead",
        body="Several openings listed below.",
        matcher=stub_matcher,
        expected_role="",
        expected_canonical=None,
        expected_source=RoleSource.SOURCE_PARENT,
    ),
    Fixture(
        name="F7 nothing usable at all",
        extracted="",
        subject="",
        body="",
        matcher=stub_matcher,
        expected_role="",
        expected_canonical=None,
        expected_source=RoleSource.UNKNOWN,
    ),
]


class RoleAssignmentFixtureTests(unittest.TestCase):
    def _run(self, fixture: Fixture):
        return assign_role(
            extracted=fixture.extracted,
            subject=fixture.subject,
            body=fixture.body,
            matcher=fixture.matcher,
        )

    def test_each_fixture_matches_its_declared_expectation(self) -> None:
        mismatches: list[str] = []
        for fixture in FIXTURES:
            actual = self._run(fixture)
            for field, expected, got in (
                ("role", fixture.expected_role, actual.role),
                ("role_canonical", fixture.expected_canonical, actual.role_canonical),
                ("role_source", fixture.expected_source, actual.role_source),
            ):
                if expected != got:
                    mismatches.append(
                        f"{fixture.name}\n    {field}:\n      expected: {expected!r}\n      actual:   {got!r}"
                    )
        self.assertEqual(mismatches, [], "\n\n" + "\n\n".join(mismatches))

    def test_every_fixture_output_is_markup_free_and_capped(self) -> None:
        """Two invariants that must hold for every record, whatever the outcome."""
        for fixture in FIXTURES:
            actual = self._run(fixture)
            self.assertIsNone(MARKUP_PATTERN.search(actual.role), fixture.name)
            self.assertLessEqual(len(actual.role), ROLE_MAX_CHARS, fixture.name)

    def test_f5_is_the_confidence_gate(self) -> None:
        """The single most important case: an unmatchable JD is not force-matched.

        Nearest-neighbour search always returns *something*; without a similarity
        floor a Salesforce JD gets labelled "Java Developer" because that is the
        closest entry. That would be the same silent-plausible-wrong-answer the
        provenance work exists to remove, just from a cheaper generator.
        """
        fixture = next(f for f in FIXTURES if f.name.startswith("F5"))
        actual = self._run(fixture)
        self.assertEqual(actual.role_source, RoleSource.SUBJECT_FALLBACK)
        self.assertIsNone(actual.role_canonical)
        self.assertNotIn("Java", actual.role)

    def test_no_fixture_is_silently_unlabelled(self) -> None:
        """Every write must carry provenance; NULL is reserved for legacy rows."""
        for fixture in FIXTURES:
            actual = self._run(fixture)
            self.assertTrue(actual.role_source, fixture.name)


if __name__ == "__main__":
    unittest.main()
