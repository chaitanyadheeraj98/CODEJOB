"""The vocabulary, the fingerprint and the composer.

Three write paths now reach the profile - the assistant's offer, a user-directed
save, and the Settings control - and all three compose an entry through this
module. A rule that only holds in one of them is a rule that does not hold.
"""

import os
import unittest

os.environ["DEBUG"] = "false"

from fastapi import HTTPException

from app.services.candidate_profile_service import (
    CANDIDATE_PROFILE_MAX_CHARS,
    PROFILE_FIELDS,
    SAVED_HEADING,
    canonical_field,
    compose_append,
    compose_entry,
    conflicting_statements,
    entry_field,
    fingerprint,
    plan_append,
    validate_profile_text,
)

PROFILE = """# Chaithanya Dheeraj
- Work Authorization: H1B
- Passport: X1234567
"""


class CanonicalFieldTests(unittest.TestCase):
    def test_the_label_itself_resolves_whatever_its_case(self) -> None:
        self.assertEqual(canonical_field("Notice period"), "Notice period")
        self.assertEqual(canonical_field("NOTICE PERIOD"), "Notice period")
        self.assertEqual(canonical_field("  notice period  "), "Notice period")

    def test_an_alias_resolves_to_the_canonical_label(self) -> None:
        # The assistant asks "what is your visa status?"; the label stored is
        # the registry's, never the phrasing of the question.
        self.assertEqual(canonical_field("visa status"), "Work Authorization")
        self.assertEqual(canonical_field("relocate"), "Willing to relocate")

    def test_a_field_outside_the_registry_is_refused_rather_than_invented(self) -> None:
        self.assertIsNone(canonical_field("Favourite colour"))
        self.assertIsNone(canonical_field(""))


class FingerprintTests(unittest.TestCase):
    def test_the_same_text_always_fingerprints_the_same(self) -> None:
        self.assertEqual(fingerprint(PROFILE), fingerprint(PROFILE))
        self.assertEqual(len(fingerprint(PROFILE)), 64)

    def test_one_changed_character_changes_the_fingerprint(self) -> None:
        self.assertNotEqual(fingerprint(PROFILE), fingerprint(PROFILE + " "))

    def test_an_empty_profile_still_has_one(self) -> None:
        self.assertEqual(len(fingerprint("")), 64)


class ComposeEntryTests(unittest.TestCase):
    def test_field_mode_writes_label_colon_value(self) -> None:
        self.assertEqual(
            compose_entry("Notice period", "2 weeks", verbatim=False),
            "- Notice period: 2 weeks",
        )

    def test_verbatim_mode_quotes_the_sentence_under_a_registry_label(self) -> None:
        self.assertEqual(
            compose_entry("Notice period", "I can join after two weeks", verbatim=True),
            '- Notice period (in the user\'s words): "I can join after two weeks"',
        )

    def test_no_entry_carries_a_timestamp(self) -> None:
        # A date inside the value is a value the model reads as authoritative and
        # can leak into a drafted email. Staleness lives on uploaded_at instead.
        for verbatim in (False, True):
            entry = compose_entry("Notice period", "2 weeks", verbatim=verbatim)
            self.assertNotIn("20", entry.replace("2 weeks", ""))
            self.assertNotIn("saved", entry.lower())


class ComposeAppendTests(unittest.TestCase):
    def test_the_heading_is_added_once_and_only_once(self) -> None:
        first = compose_append(PROFILE, "- Notice period: 2 weeks")
        second = compose_append(first, "- Rate: $75/hr")

        self.assertEqual(first.count(SAVED_HEADING), 1)
        self.assertEqual(second.count(SAVED_HEADING), 1)
        self.assertIn("- Notice period: 2 weeks", second)
        self.assertIn("- Rate: $75/hr", second)

    def test_nothing_already_in_the_document_is_reordered_or_rewritten(self) -> None:
        combined = compose_append(PROFILE, "- Notice period: 2 weeks")

        self.assertTrue(combined.startswith("# Chaithanya Dheeraj"))
        self.assertIn("- Work Authorization: H1B", combined)
        self.assertIn("- Passport: X1234567", combined)

    def test_a_section_below_the_saved_heading_survives_an_append(self) -> None:
        existing = f"{PROFILE}\n{SAVED_HEADING}\n- Rate: $75/hr\n\n## My own notes\n- Prefer remote\n"

        combined = compose_append(existing, "- Notice period: 2 weeks")

        self.assertIn("## My own notes", combined)
        self.assertIn("- Prefer remote", combined)
        # The new line lands in the saved section, above the user's own heading.
        self.assertLess(combined.index("- Notice period: 2 weeks"), combined.index("## My own notes"))

    def test_a_field_the_saved_block_already_holds_is_updated_not_repeated(self) -> None:
        """The case this rule exists for: correcting a value already saved.

        Two `- Work Authorization:` lines in the block the system prompt tells
        the model to believe leave it no way to tell which half is current, and
        it will draft mail from either.
        """
        saved = compose_append(PROFILE, "- Work Authorization: GC")
        combined, replaced = plan_append(saved, "- Work Authorization: EAD")

        self.assertEqual(replaced, ["- Work Authorization: GC"])
        self.assertIn("- Work Authorization: EAD", combined)
        self.assertNotIn("- Work Authorization: GC", combined)
        # Once in the user's own text, once in ours - and not a third time.
        self.assertEqual(combined.count("Work Authorization"), 2)

    def test_a_correction_replaces_a_verbatim_entry_for_the_same_field(self) -> None:
        """One field, one line - whichever of the two shapes holds it."""
        saved = compose_append(
            PROFILE, '- Notice period (in the user\'s words): "I can join after two weeks"'
        )
        combined, replaced = plan_append(saved, "- Notice period: 2 weeks")

        self.assertEqual(len(replaced), 1)
        self.assertNotIn("in the user's words", combined)
        self.assertIn("- Notice period: 2 weeks", combined)

    def test_duplicates_already_in_the_document_are_collapsed(self) -> None:
        """A profile written before this rule can already hold several."""
        existing = (
            f"{PROFILE}\n{SAVED_HEADING}\n- Work Authorization: H1B\n"
            "- Rate: $75/hr\n- Work Authorization: GC\n"
        )
        combined, replaced = plan_append(existing, "- Work Authorization: EAD")

        self.assertEqual(replaced, ["- Work Authorization: H1B", "- Work Authorization: GC"])
        # Counted inside the saved section only: PROFILE carries its own
        # `- Work Authorization: H1B` above the heading, which stays untouched.
        saved_block = combined.split(SAVED_HEADING, 1)[1]
        self.assertEqual(saved_block.count("- Work Authorization:"), 1)
        self.assertIn("- Work Authorization: EAD", saved_block)
        self.assertIn("- Rate: $75/hr", combined)

    def test_a_line_outside_the_saved_section_is_never_rewritten(self) -> None:
        """PROFILE's own `- Work Authorization: H1B` is the user's document.

        Rewriting it is what the trust boundary forbids, so the new value is
        added below and the two coexist. `conflicting_statements` is what tells
        the user their profile now says both.
        """
        combined, replaced = plan_append(PROFILE, "- Work Authorization: GC")

        self.assertEqual(replaced, [])
        self.assertIn("- Work Authorization: H1B", combined)
        self.assertEqual(
            conflicting_statements(PROFILE, "Work Authorization"),
            ["- Work Authorization: H1B"],
        )

    def test_entry_field_reads_back_both_composed_shapes(self) -> None:
        self.assertEqual(entry_field("- Work Authorization: GC"), "Work Authorization")
        self.assertEqual(
            entry_field('- Notice period (in the user\'s words): "after two weeks"'),
            "Notice period",
        )
        self.assertIsNone(entry_field("- Something we never write: x"))

    def test_appending_to_an_empty_profile_is_refused(self) -> None:
        # Appending to nothing is creating, and creating a profile is an upload.
        for empty in ("", "   \n\t "):
            with self.assertRaises(HTTPException) as raised:
                compose_append(empty, "- Notice period: 2 weeks")
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("Settings", raised.exception.detail)

    def test_an_append_that_would_cross_the_limit_is_refused_with_both_numbers(self) -> None:
        existing = "x" * (CANDIDATE_PROFILE_MAX_CHARS - 5)

        with self.assertRaises(HTTPException) as raised:
            compose_append(existing, "- Notice period: 2 weeks")

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn(f"{CANDIDATE_PROFILE_MAX_CHARS:,}", raised.exception.detail)


class ValidateProfileTextTests(unittest.TestCase):
    def test_it_accepts_bytes_and_str_alike(self) -> None:
        self.assertEqual(validate_profile_text(b"# Me\n", "p.md"), "# Me")
        self.assertEqual(validate_profile_text("# Me\n", "p.md"), "# Me")

    def test_every_rejection_names_what_failed(self) -> None:
        with self.assertRaises(HTTPException) as empty:
            validate_profile_text(b"", "p.md")
        self.assertIn("empty", empty.exception.detail)

        with self.assertRaises(HTTPException) as blank:
            validate_profile_text(b"   \n", "p.md")
        self.assertIn("no text", blank.exception.detail)

        with self.assertRaises(HTTPException) as encoding:
            validate_profile_text(b"\xff\xfe\x00bad", "p.md")
        self.assertIn("UTF-8", encoding.exception.detail)

        with self.assertRaises(HTTPException) as too_long:
            validate_profile_text(b"x" * (CANDIDATE_PROFILE_MAX_CHARS + 1), "p.md")
        self.assertIn(f"{CANDIDATE_PROFILE_MAX_CHARS:,}", too_long.exception.detail)


class RegistryDriftTests(unittest.TestCase):
    def test_every_field_the_prompt_names_is_one_the_registry_can_write(self) -> None:
        """Registry drift is silent: the model asks for a field it cannot save.

        The prompt's prose list is the vocabulary the user sees; this pins the
        two together.
        """
        from app.ai.chat.system_prompt import build_system_prompt

        prompt = build_system_prompt(PROFILE).lower()
        for label in PROFILE_FIELDS:
            self.assertIn(
                label.lower().split()[0],
                prompt,
                f"{label} is writable but the prompt never mentions it",
            )


if __name__ == "__main__":
    unittest.main()
