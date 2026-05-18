import unittest
from types import SimpleNamespace

from app.main import _compose_gmail_query, _maybe_generate_cold_call_script


class RunOnceHotfixTests(unittest.TestCase):
    def test_compose_gmail_query_dedupes_is_unread(self) -> None:
        query = _compose_gmail_query(
            "java is:unread",
            "2026-05-11",
            {
                "version": 1,
                "query": {
                    "force_unread": True,
                    "include_labels": [],
                    "exclude_labels": [],
                    "date_mode": "custom",
                },
                "run": {"run_mode": "all", "batch_limit": 20, "dry_run": False},
                "qualification": {
                    "location_strictness": "balanced",
                    "score_threshold_override_enabled": False,
                    "score_threshold_override_value": 0.6,
                },
            },
        )
        self.assertEqual(query.lower().count("is:unread"), 1)

    def test_maybe_generate_cold_call_script_handles_none_script(self) -> None:
        email = SimpleNamespace(
            is_premium=True,
            cold_call_script=None,
            cold_call_script_source=None,
            cold_call_script_error=None,
            recruiter_phone="+1 214 555 1212",
            sender="Recruiter <r@example.com>",
            subject="Role",
            body="Body",
            role="Java Developer",
        )
        user_settings = SimpleNamespace(feature_ai_enabled=False)
        _maybe_generate_cold_call_script(email, resume=None, user_settings=user_settings)
        self.assertEqual(email.cold_call_script, "")
        self.assertIn("No active resume", email.cold_call_script_error or "")

    def test_maybe_generate_cold_call_script_non_premium_clears_fields(self) -> None:
        email = SimpleNamespace(
            is_premium=False,
            cold_call_script=None,
            cold_call_script_source="deepseek",
            cold_call_script_error="old",
            recruiter_phone=None,
            sender="Recruiter <r@example.com>",
            subject="Role",
            body="Body",
            role="Java Developer",
        )
        user_settings = SimpleNamespace(feature_ai_enabled=False)
        _maybe_generate_cold_call_script(email, resume=None, user_settings=user_settings)
        self.assertEqual(email.cold_call_script, "")
        self.assertIsNone(email.cold_call_script_source)
        self.assertIsNone(email.cold_call_script_error)


if __name__ == "__main__":
    unittest.main()
