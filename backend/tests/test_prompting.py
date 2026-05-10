import unittest

from app.ai.prompting import build_reply_prompts


class PromptingTests(unittest.TestCase):
    def test_prompt_includes_layered_contract_and_greeting_rule(self) -> None:
        _, user_prompt = build_reply_prompts(
            sender="Recruiter <sender@example.com>",
            recruiter_to_email="jobs@example.com",
            greeting_line="Hi,",
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
        self.assertIn('Use this greeting exactly as the first greeting line: "Hi,"', user_prompt)
        self.assertIn("Visa: H1B", user_prompt)


if __name__ == "__main__":
    unittest.main()
