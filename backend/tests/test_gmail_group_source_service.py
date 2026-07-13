import unittest

from app.services.gmail_group_source_service import (
    ConfiguredRequirementGroup,
    normalize_google_group_email,
    parse_group_inputs,
    resolve_trusted_group_context,
)


class GmailGroupSourceServiceTests(unittest.TestCase):
    def test_normalizes_google_group_unsubscribe_address(self) -> None:
        self.assertEqual(
            normalize_google_group_email("usitjobshub+unsubscribe@googlegroups.com"),
            "usitjobshub@googlegroups.com",
        )

    def test_normalizes_google_group_url_to_address(self) -> None:
        self.assertEqual(
            normalize_google_group_email("https://groups.google.com/g/C2C-Corp2Corp-Jobs"),
            "c2c-corp2corp-jobs@googlegroups.com",
        )

    def test_bulk_parser_accepts_emails_and_urls(self) -> None:
        parsed = parse_group_inputs(
            "C2C Jobs | https://groups.google.com/g/C2C-Corp2Corp-Jobs\n"
            "usitjobshub@googlegroups.com\n"
        )
        self.assertEqual(
            parsed,
            [
                ("c2c-corp2corp-jobs@googlegroups.com", "C2C Jobs"),
                ("usitjobshub@googlegroups.com", None),
            ],
        )

    def test_resolve_prefers_list_post_over_footer(self) -> None:
        groups = [
            ConfiguredRequirementGroup(
                id=1,
                display_name="C2C Corp2Corp Jobs",
                group_email="c2c-corp2corp-jobs@googlegroups.com",
                normalized_group_email="c2c-corp2corp-jobs@googlegroups.com",
                group_slug="C2C-Corp2Corp-Jobs",
                enabled=True,
            )
        ]
        context = resolve_trusted_group_context(
            groups=groups,
            subject="[C2C-Corp2Corp-Jobs] Workday role",
            body="Footer only c2c-corp2corp-jobs+unsubscribe@googlegroups.com",
            list_post="<mailto:c2c-corp2corp-jobs@googlegroups.com>",
        )
        self.assertTrue(context.matched)
        self.assertEqual(context.group_email, "c2c-corp2corp-jobs@googlegroups.com")
        self.assertEqual(context.match_method, "list_post")

    def test_resolve_can_match_subject_prefix(self) -> None:
        groups = [
            ConfiguredRequirementGroup(
                id=2,
                display_name="US IT Jobs Hub",
                group_email="usitjobshub@googlegroups.com",
                normalized_group_email="usitjobshub@googlegroups.com",
                group_slug="usitjobshub",
                enabled=True,
            )
        ]
        context = resolve_trusted_group_context(
            groups=groups,
            subject="[usitjobshub] Immediate Requirement",
            body="QA Automation role in Pittsburgh.",
        )
        self.assertTrue(context.matched)
        self.assertEqual(context.match_method, "subject_prefix")

    def test_resolve_does_not_use_poster_from_only(self) -> None:
        groups = [
            ConfiguredRequirementGroup(
                id=3,
                display_name="US IT Jobs Hub",
                group_email="usitjobshub@googlegroups.com",
                normalized_group_email="usitjobshub@googlegroups.com",
                group_slug="usitjobshub",
                enabled=True,
            )
        ]
        context = resolve_trusted_group_context(
            groups=groups,
            subject="Requirement",
            body="Body without any group metadata.",
            to_header="Recruiter Person <recruiter@example.com>",
        )
        self.assertFalse(context.matched)


if __name__ == "__main__":
    unittest.main()
