import unittest

from app.mcp_server.tools.navigation import NAVIGABLE_PAGES, navigate_to_queue


class NavigateToQueueTests(unittest.TestCase):
    def test_every_resume_tracking_tab_is_navigable(self) -> None:
        for tab in ("gaps", "resumes", "submissions", "manage", "editor"):
            result = navigate_to_queue("resume_tracking", tab=tab)
            self.assertEqual(result["tab"], tab)
            self.assertEqual(result["dropped"], [])

    def test_returns_a_navigable_payload_for_a_known_page(self) -> None:
        payload = navigate_to_queue(
            "needs_review",
            filters={"role": "Java Developer", "date_filter": "last_7_days"},
            title="Java roles from last week",
        )

        self.assertEqual(payload["action"], "navigate_to_queue")
        self.assertEqual(payload["page"], "needs_review")
        self.assertEqual(payload["label"], "Needs Review")
        self.assertEqual(payload["title"], "Java roles from last week")
        self.assertEqual(payload["filters"], {"role": "Java Developer", "date_filter": "last_7_days"})
        self.assertEqual(payload["dropped"], [])

    def test_unknown_page_is_refused_with_the_valid_list(self) -> None:
        payload = navigate_to_queue("atlantis")

        self.assertIn("error", payload)
        self.assertEqual(payload["pages"], sorted(NAVIGABLE_PAGES))
        self.assertNotIn("action", payload)

    def test_unknown_filter_key_is_dropped_and_reported(self) -> None:
        # The server should not emit a payload the client will only reject, and
        # a filter the user believes was applied is worse than a refused one.
        payload = navigate_to_queue("needs_review", filters={"role": "Java", "unreplied": "true"})

        self.assertEqual(payload["filters"], {"role": "Java"})
        self.assertEqual(payload["dropped"], [{"key": "unreplied", "reason": "unknown_field"}])

    def test_tab_is_validated_and_falls_back_to_the_page_default(self) -> None:
        valid = navigate_to_queue("premium_numbers", tab="opportunities")
        self.assertEqual(valid["tab"], "opportunities")
        self.assertEqual(valid["dropped"], [])

        invalid = navigate_to_queue("premium_numbers", tab="nowhere")
        self.assertEqual(invalid["tab"], "inventory")
        self.assertEqual(invalid["dropped"], [{"key": "tab", "reason": "unknown_tab"}])

        untabbed = navigate_to_queue("needs_review", tab="opportunities")
        self.assertIsNone(untabbed["tab"])
        self.assertEqual(untabbed["dropped"], [{"key": "tab", "reason": "unknown_tab"}])

    def test_title_and_values_are_clipped(self) -> None:
        payload = navigate_to_queue("needs_review", filters={"role": "R" * 400}, title="T" * 400)

        self.assertEqual(len(payload["title"]), 120)
        self.assertEqual(len(payload["filters"]["role"]), 100)

    def test_page_with_no_tabs_reports_no_tab(self) -> None:
        payload = navigate_to_queue("recent_runs")

        self.assertIsNone(payload["tab"])
        self.assertEqual(payload["filters"], {})


if __name__ == "__main__":
    unittest.main()
