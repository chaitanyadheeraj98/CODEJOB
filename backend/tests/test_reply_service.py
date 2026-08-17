import unittest

from app.ai import reply_service


class ReplyServiceTests(unittest.TestCase):
    def test_compose_reply_uses_single_backend_owned_greeting_and_signature(self) -> None:
        ai_text = """Subject: Senior API Integration Engineer - Chaithanya Dheeraj N

Dear Recruiter,

Dear Recruiter,

I am writing to express my strong interest in the Senior API Integration Engineer position.

Best regards,
Someone Else
"""
        fallback_draft = """Subject: Application for Senior API Integration Engineer

Dear Recruiter,

Fallback body

Best regards,
Chaithanya Dheeraj N
[PHONE] +1 940-629-6920
[EMAIL] chaithanyadheeraj1026@gmail.com"""

        composed = reply_service._compose_reply_with_fixed_wrapper(
            ai_text,
            expected_greeting="Dear Recruiter,",
            fallback_draft=fallback_draft,
        )

        self.assertEqual(composed.count("Dear Recruiter,"), 1)
        self.assertIn("Subject: Senior API Integration Engineer - Chaithanya Dheeraj N", composed)
        self.assertIn("I am writing to express my strong interest", composed)
        self.assertIn("Best regards,\nChaithanya Dheeraj N", composed)
        self.assertNotIn("Someone Else", composed)

    def test_compose_reply_strips_nvoids_header_from_ai_output(self) -> None:
        ai_text = """Nvoids Listing: https://nvoids.com/job_details.jsp?id=1
Subject: Example Role

Dear Recruiter,

Body paragraph.
"""
        fallback_draft = """Subject: Fallback Role

Dear Recruiter,

Fallback body

Best regards,
Chaithanya Dheeraj N
[PHONE] +1 940-629-6920
[EMAIL] chaithanyadheeraj1026@gmail.com"""

        composed = reply_service._compose_reply_with_fixed_wrapper(
            ai_text,
            expected_greeting="Dear Recruiter,",
            fallback_draft=fallback_draft,
        )

        self.assertNotIn("Nvoids Listing:", composed)
        self.assertTrue(composed.startswith("Subject: Example Role"))

    def test_generate_reply_falls_back_when_ai_body_is_empty_after_cleanup(self) -> None:
        original_chat = reply_service.deepseek_chat_completion
        original_extract = reply_service.extract_resume_context
        try:
            reply_service.deepseek_chat_completion = lambda *_args, **_kwargs: "Dear Recruiter,\n\nBest regards,"
            reply_service.extract_resume_context = lambda *_args, **_kwargs: "resume context"

            fallback_draft = """Subject: Fallback Role

Dear Recruiter,

Fallback body

Best regards,
Chaithanya Dheeraj N
[PHONE] +1 940-629-6920
[EMAIL] chaithanyadheeraj1026@gmail.com"""

            result = reply_service.generate_reply_with_ai_or_fallback(
                sender="Recruiter <sender@example.com>",
                recruiter_to_email="jobs@example.com",
                greeting_line="Dear Recruiter,",
                subject="Fallback Role",
                body="Role body",
                role="Fallback Role",
                location="TX",
                salary_text="not_specified",
                skills_text="java, api",
                resume_path="resume.docx",
                resume_file_name="resume.docx",
                fallback_draft=fallback_draft,
                model_name="deepseek-chat",
            )

            self.assertEqual(result.draft_text, fallback_draft)
            self.assertEqual(result.source, "rules_only")
        finally:
            reply_service.deepseek_chat_completion = original_chat
            reply_service.extract_resume_context = original_extract


if __name__ == "__main__":
    unittest.main()
