import unittest

from app.ai.prompting import build_reply_prompts


class PromptingTests(unittest.TestCase):
    def test_prompt_includes_backend_owned_wrapper_contract(self) -> None:
        _, user_prompt = build_reply_prompts(
            sender="Recruiter <sender@example.com>",
            recruiter_to_email="jobs@example.com",
            greeting_line="Dear Recruiter,",
            subject="Backend Java Developer",
            body="Role in TX. Please share visa status and location.",
            role="Backend Java Developer",
            location="unknown",
            salary_text="not_specified",
            skills_text="java, spring boot, aws",
            resume_text="7+ years Java and cloud experience.",
        )

        self.assertIn("Strict Instructions:", user_prompt)
        self.assertIn("Output Format:", user_prompt)
        self.assertIn('Do not output any greeting line such as "Hi" or "Dear Recruiter,".', user_prompt)
        self.assertIn('Do not output any closing/signature block such as "Best regards".', user_prompt)
        self.assertIn('Subject line only once, starting with "Subject:"', user_prompt)
        self.assertIn("Visa: H1B", user_prompt)


if __name__ == "__main__":
    unittest.main()
