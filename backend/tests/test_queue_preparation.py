import unittest
from types import SimpleNamespace

from app.automation.queue_preparation import (
    QueuePreparationDependencies,
    QueuePreparationRequest,
    prepare_candidate_for_queue,
)
from app.models import UserSettings


class QueuePreparationTests(unittest.TestCase):
    def test_structured_parser_details_reach_hard_filter(self) -> None:
        received: dict[str, object] = {}

        def hard_filter(parsed, settings, policy, parser_details=None):
            received["parser_details"] = parser_details
            return False, "blocked: missing_skills:Python"

        deps = QueuePreparationDependencies(
            parse_email=lambda subject, body: {"role": "AI Engineer", "location": "remote", "salary_text": "", "skills_text": "Python"},
            hard_filter_check=hard_filter,
            compute_blended_ai_score=lambda *args: (0.9, "", "test", None, None, None),
            policy_f2f_block=lambda parsed, policy, settings: (False, ""),
            evaluate_routing_policy=lambda *args: SimpleNamespace(),
            greeting_from_to_contact=lambda *args: "Hello,",
            build_user_fallback_draft=lambda *args: "draft",
            generate_reply_with_ai_or_fallback=lambda **kwargs: SimpleNamespace(),
        )
        details = {"structured_requirements": {"required_groups": []}}
        request = QueuePreparationRequest(
            db=None,
            owner_id="default-owner",
            sender="recruiter@example.com",
            subject="AI Engineer",
            body="Required: Python",
            snippet="",
            user_settings=UserSettings(owner_id="default-owner", accepted_locations=""),
            effective_policy={},
            threshold=0.6,
            model_name="test",
            scoring_resume=None,
            draft_resume=None,
            parser_details=details,
        )

        prepare_candidate_for_queue(request, deps)

        self.assertEqual(received["parser_details"], details)


if __name__ == "__main__":
    unittest.main()
