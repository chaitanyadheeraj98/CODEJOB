import json
import unittest

from app.models import RecruiterEmail
from app.services.sendability_service import apply_resume_sendability, resolve_sendability_status


class SendabilityServiceTests(unittest.TestCase):
    @staticmethod
    def _email(**overrides) -> RecruiterEmail:
        values = {
            "owner_id": "default-owner",
            "sender": "recruiter@example.com",
            "subject": "Role",
            "body": "Job description",
            "state": "needs_review",
            "decision": "Qualified",
            "source": "gmail",
            "screening_mode": "strict",
            "draft_reply": "Please consider my resume.",
            "draft_source": "rules_only",
            "draft_model": "rules",
            "resume_asset_id": 42,
            "resume_file_name": "resume.pdf",
        }
        values.update(overrides)
        return RecruiterEmail(**values)

    def test_compatibility_mode_keeps_mandatory_failure_diagnostic(self) -> None:
        email = RecruiterEmail(
            owner_id="default-owner",
            sender="recruiter@example.com",
            subject="Role",
            body="Job description",
            state="needs_review",
            decision="Qualified",
            source="gmail",
            screening_mode="compatibility",
            draft_reply="Please consider my resume.",
            resume_asset_id=42,
            resume_file_name="resume.pdf",
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "fail"}),
        )

        status = apply_resume_sendability(email)

        self.assertEqual(status, "sendable")
        self.assertEqual(resolve_sendability_status(email), "sendable")
        self.assertEqual(email.draft_reply, "Please consider my resume.")
        self.assertEqual(email.resume_asset_id, 42)
        self.assertEqual(email.resume_file_name, "resume.pdf")

    def test_strict_mode_clears_sendable_material_on_mandatory_failure(self) -> None:
        email = RecruiterEmail(
            owner_id="default-owner",
            sender="recruiter@example.com",
            subject="Role",
            body="Job description",
            state="needs_review",
            decision="Qualified",
            source="gmail",
            screening_mode="strict",
            eligibility_status="pass",
            draft_reply="Please consider my resume.",
            draft_source="rules_only",
            resume_asset_id=42,
            resume_file_name="resume.pdf",
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "fail"}),
        )

        status = apply_resume_sendability(email)

        self.assertEqual(status, "mandatory_resume_fail")
        self.assertEqual(resolve_sendability_status(email), "mandatory_resume_fail")
        self.assertEqual(email.draft_reply, "")
        self.assertIsNone(email.draft_source)
        self.assertIsNone(email.draft_model)
        self.assertIsNone(email.resume_asset_id)
        self.assertIsNone(email.resume_file_name)

    def test_historical_none_mode_preserves_stored_mandatory_status_when_resolving(self) -> None:
        email = self._email(
            screening_mode=None,
            sendability_status="mandatory_resume_fail",
        )

        self.assertEqual(resolve_sendability_status(email), "mandatory_resume_fail")
        self.assertEqual(apply_resume_sendability(email), "sendable")
        self.assertEqual(email.draft_reply, "Please consider my resume.")
        self.assertEqual(email.resume_asset_id, 42)

    def test_structural_status_is_never_recomputed(self) -> None:
        email = self._email(
            sendability_status="manifest_review",
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "pass"}),
        )

        self.assertEqual(resolve_sendability_status(email), "manifest_review")
        self.assertEqual(apply_resume_sendability(email), "manifest_review")

    def test_score_review_preserves_draft_but_blocks_sendability(self) -> None:
        email = self._email(
            screening_mode="compatibility",
            decision="Reject",
            qualification_result="rejected",
            blocking_rule="score_threshold",
            sendability_status="score_review",
        )

        self.assertEqual(resolve_sendability_status(email), "score_review")
        self.assertEqual(apply_resume_sendability(email), "score_review")
        self.assertEqual(email.draft_reply, "Please consider my resume.")
        self.assertEqual(email.resume_asset_id, 42)

    def test_compatibility_mode_clears_strict_eligibility_blocker_when_draft_exists(self) -> None:
        email = self._email(
            screening_mode="compatibility",
            sendability_status="blocked_ineligible",
        )

        self.assertEqual(apply_resume_sendability(email), "sendable")
        self.assertEqual(email.sendability_status, "sendable")

    def test_strict_review_blocks_only_when_required_groups_exist(self) -> None:
        required = self._email(
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "needs_review"}),
            parser_details_json=json.dumps(
                {"structured_requirements": {"required_groups": [{"skills": [{"canonical_name": "Java"}]}]}}
            ),
        )
        optional = self._email(
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "review"}),
            parser_details_json=json.dumps({"structured_requirements": {"required_groups": []}}),
        )
        optional_without_draft = self._email(
            draft_reply="",
            resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": "review"}),
            parser_details_json=json.dumps({"structured_requirements": {"required_groups": []}}),
        )

        self.assertEqual(apply_resume_sendability(required), "mandatory_resume_review")
        self.assertEqual(required.draft_reply, "")
        self.assertEqual(apply_resume_sendability(optional), "sendable")
        self.assertEqual(optional.draft_reply, "Please consider my resume.")
        self.assertEqual(resolve_sendability_status(optional_without_draft), "not_ready")
        self.assertEqual(apply_resume_sendability(optional_without_draft), "sendable")

    def test_strict_pass_not_applicable_and_missing_gate_keep_current_fallbacks(self) -> None:
        for gate_status in ("pass", "not_applicable"):
            with self.subTest(gate_status=gate_status):
                email = self._email(
                    resume_picker_breakdown_json=json.dumps({"mandatory_gate_status": gate_status})
                )
                self.assertEqual(apply_resume_sendability(email), "sendable")

        no_draft = self._email(
            draft_reply="",
            resume_picker_breakdown_json=json.dumps({}),
        )
        self.assertEqual(apply_resume_sendability(no_draft), "not_ready")
        self.assertIsNone(no_draft.sendability_status)


if __name__ == "__main__":
    unittest.main()
