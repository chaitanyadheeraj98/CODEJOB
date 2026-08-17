import unittest

from app.models import UserSettings
from app.services.candidate_runtime_service import (
    CandidateRuntimeDeps,
    CandidateRuntimeService,
    resolve_resume_display_name,
)


class ResumeDisplayNameTests(unittest.TestCase):
    def _settings(self, *, resume_display_name: str = "", fallback_draft_template: str = "{{resume_file_name}}") -> UserSettings:
        return UserSettings(
            owner_id="default-owner",
            enabled=True,
            gmail_query="is:unread",
            default_gmail_query="is:unread",
            default_date_mode="today",
            fallback_draft_template=fallback_draft_template,
            signature_name="Tester",
            signature_phone="+1",
            signature_email="tester@example.com",
            resume_display_name=resume_display_name,
            policy_json="",
        )

    def _service(self) -> CandidateRuntimeService:
        deps = CandidateRuntimeDeps(
            get_settings=lambda _db: self._settings(),
            evaluate_routing_policy=lambda *_args, **_kwargs: None,
            apply_routing_decision=lambda *_args, **_kwargs: None,
        )
        return CandidateRuntimeService(deps)

    def test_resolve_resume_display_name_falls_back_to_real_variant_name_when_setting_blank(self) -> None:
        result = resolve_resume_display_name(self._settings(resume_display_name=""), "resume-best-match.pdf")
        self.assertEqual(result, "resume-best-match.pdf")

    def test_resolve_resume_display_name_preserves_selected_variant_extension(self) -> None:
        result = resolve_resume_display_name(
            self._settings(resume_display_name="Chaithanya Dheeraj Resume"),
            "resume-best-match.docx",
        )
        self.assertEqual(result, "Chaithanya Dheeraj Resume.docx")

    def test_resolve_resume_display_name_ignores_user_supplied_extension_and_uses_variant_extension(self) -> None:
        result = resolve_resume_display_name(
            self._settings(resume_display_name="Chaithanya Dheeraj Resume.pdf"),
            "resume-best-match.docx",
        )
        self.assertEqual(result, "Chaithanya Dheeraj Resume.docx")

    def test_resolve_resume_display_name_treats_whitespace_value_as_disabled(self) -> None:
        result = resolve_resume_display_name(self._settings(resume_display_name="   "), "resume-best-match.pdf")
        self.assertEqual(result, "resume-best-match.pdf")

    def test_build_user_fallback_draft_uses_public_resume_name_for_newly_generated_drafts(self) -> None:
        service = self._service()
        draft = service.build_user_fallback_draft(
            None,
            self._settings(resume_display_name="Chaithanya Dheeraj Resume"),
            sender="Recruiter <r@example.com>",
            role="Java Developer",
            parsed={
                "location": "TX",
                "salary_text": "$60/hr",
                "skills_text": "Java, Spring Boot",
                "asks_contact_fields": False,
            },
            greeting_line="Dear Recruiter,",
            resume_file_name=resolve_resume_display_name(
                self._settings(resume_display_name="Chaithanya Dheeraj Resume"),
                "resume-best-match.pdf",
            ),
        )
        self.assertEqual(draft, "Chaithanya Dheeraj Resume.pdf")


if __name__ == "__main__":
    unittest.main()
