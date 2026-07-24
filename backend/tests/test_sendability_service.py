import json
import unittest

from app.models import RecruiterEmail
from app.services.sendability_service import apply_resume_sendability, resolve_sendability_status


class SendabilityServiceTests(unittest.TestCase):
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
        self.assertIsNone(email.resume_asset_id)
        self.assertIsNone(email.resume_file_name)


if __name__ == "__main__":
    unittest.main()
