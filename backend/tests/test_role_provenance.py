"""Unit tests for the role cleaning + assignment ladder.

Cases are drawn from real production values (measured 2026-09-01), not invented
shapes: the `<br />` bleed, the 6,632-character nvoids scraper row, and the
"N Requirements ::" multi-role subjects.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from app.services.role_provenance import (
    MARKUP_PATTERN,
    ROLE_MAX_CHARS,
    RoleSource,
    TaxonomyMatch,
    assign_role,
    is_multi_role_subject,
    normalize_role,
)


class NormalizeRoleTests(unittest.TestCase):
    def test_empty_inputs(self) -> None:
        for value in (None, "", "   ", "\n\n"):
            self.assertEqual(normalize_role(value), "")

    def test_clean_title_is_untouched(self) -> None:
        self.assertEqual(normalize_role("Java Full Stack Developer"), "Java Full Stack Developer")

    def test_strips_br_bleed_and_keeps_only_the_title(self) -> None:
        raw = "Java Architect with AI experience<br /><br />Location: Austin, TX -Onsite<br /><br />Skills-<br />"
        result = normalize_role(raw)
        self.assertEqual(result, "Java Architect with AI experience")
        self.assertIsNone(MARKUP_PATTERN.search(result))

    def test_strips_nvoids_table_markup(self) -> None:
        raw = 'Java AWS Developer At Plano, TX</a><td></tr><tr><td><a href="https://jobs.nvoids.com/job_detail">'
        result = normalize_role(raw)
        self.assertIsNone(MARKUP_PATTERN.search(result))
        self.assertIn("Java AWS Developer", result)

    def test_caps_a_pasted_job_description(self) -> None:
        raw = "Senior Java Developer " + ("responsibilities and requirements " * 400)
        result = normalize_role(raw)
        self.assertLessEqual(len(result), ROLE_MAX_CHARS)
        self.assertTrue(result.startswith("Senior Java Developer"))

    def test_is_idempotent(self) -> None:
        """The write path and the tests both re-apply this; clipping must settle."""
        for raw in (
            "Java Architect with AI experience<br /><br />Location: Austin",
            "Senior Java Developer " + ("x " * 400),
            "Java Developer",
        ):
            once = normalize_role(raw)
            self.assertEqual(normalize_role(once), once)

    def test_trailing_separators_removed(self) -> None:
        self.assertEqual(normalize_role("Java Developer - "), "Java Developer")
        self.assertEqual(normalize_role("  :: Java Developer ::"), "Java Developer")


class MultiRoleSubjectTests(unittest.TestCase):
    def test_detects_numbered_requirement_blasts(self) -> None:
        for value in (
            "3 Requirements :: Java Software Engineer :: Java Senior Developer :: Senior SAP",
            "2 Position ## Looking only local candidate ## F2F interview required",
            "7 Requirements :: MSD SCM Consultant :: The D365 Finance Consultant",
            "5 Openings - Java, Python, QA",
        ):
            self.assertTrue(is_multi_role_subject(value), value)

    def test_detects_semicolon_and_colon_blasts(self) -> None:
        value = "C2C IT POSITIONS :::Business Analyst (Pharma Domain );Pega Data Migration Engineer ;Guidewire Dev"
        self.assertTrue(is_multi_role_subject(value))

    def test_does_not_flag_ordinary_titles(self) -> None:
        for value in (
            "Java Full Stack Developer",
            "Java Developer :: Remote",
            "Senior Java/SAP Commerce Cloud Developer",
            "QA Automation Engineer (Karate Framework)",
            "",
            None,
        ):
            self.assertFalse(is_multi_role_subject(value), value)


class AssignRoleTests(unittest.TestCase):
    def test_extraction_wins(self) -> None:
        result = assign_role(extracted="Java Full Stack Developer", subject="Urgent hiring")
        self.assertEqual(result.role, "Java Full Stack Developer")
        self.assertEqual(result.role_source, RoleSource.EXTRACTED)
        self.assertIsNone(result.role_canonical)

    def test_extracted_value_is_cleaned(self) -> None:
        result = assign_role(extracted="Java Architect<br /><br />Location: Austin", subject="x")
        self.assertEqual(result.role, "Java Architect")
        self.assertEqual(result.role_source, RoleSource.EXTRACTED)

    def test_multi_role_subject_is_refused_a_title(self) -> None:
        result = assign_role(extracted="", subject="3 Requirements :: Java :: SAP :: QA")
        self.assertEqual(result.role, "")
        self.assertEqual(result.role_source, RoleSource.SOURCE_PARENT)

    def test_multi_role_check_runs_before_the_matcher(self) -> None:
        """A container must never be handed to the matcher - it has no single role."""
        calls: list[str] = []

        def matcher(text: str, body: str) -> TaxonomyMatch | None:
            calls.append(text)
            return TaxonomyMatch("Java Developer")

        result = assign_role(
            extracted="", subject="4 Requirements :: A :: B :: C", matcher=matcher
        )
        self.assertEqual(result.role_source, RoleSource.SOURCE_PARENT)
        self.assertEqual(calls, [])

    def test_subject_fallback_when_no_matcher(self) -> None:
        result = assign_role(extracted=None, subject="Urgent Hiring for Java Developer")
        self.assertEqual(result.role, "Urgent Hiring for Java Developer")
        self.assertEqual(result.role_source, RoleSource.SUBJECT_FALLBACK)
        self.assertIsNone(result.role_canonical)

    def test_taxonomy_match_is_preferred_over_a_raw_subject(self) -> None:
        result = assign_role(
            extracted=None,
            subject="NO //CPT :: Urgent Hiring !!!",
            body="We need a Java engineer.",
            matcher=lambda text, body: TaxonomyMatch("Java Developer"),
        )
        self.assertEqual(result.role, "Java Developer")
        self.assertEqual(result.role_canonical, "Java Developer")
        self.assertEqual(result.role_source, RoleSource.TAXONOMY_MATCHED)

    def test_matcher_returning_none_falls_through_to_subject(self) -> None:
        """The confidence gate: no confident match must never force a value."""
        result = assign_role(
            extracted=None,
            subject="Salesforce Administrator opening",
            body="Salesforce admin work.",
            matcher=lambda text, body: None,
        )
        self.assertEqual(result.role, "Salesforce Administrator opening")
        self.assertEqual(result.role_source, RoleSource.SUBJECT_FALLBACK)

    def test_nothing_usable_is_unknown(self) -> None:
        result = assign_role(extracted="", subject="")
        self.assertEqual(result.role, "")
        self.assertEqual(result.role_source, RoleSource.UNKNOWN)

    def test_every_outcome_is_markup_free_and_capped(self) -> None:
        cases = [
            {"extracted": "Java Dev<br />x" * 300, "subject": "s"},
            {"extracted": None, "subject": "<b>Urgent</b> Java role " + ("y " * 300)},
            {"extracted": "", "subject": "3 Requirements :: A :: B :: C"},
        ]
        for case in cases:
            result = assign_role(**case)
            self.assertIsNone(MARKUP_PATTERN.search(result.role), case)
            self.assertLessEqual(len(result.role), ROLE_MAX_CHARS, case)


if __name__ == "__main__":
    unittest.main()
