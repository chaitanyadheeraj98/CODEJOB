import unittest

from app.premium_numbers.prompting import build_premium_numbers_prompts


class PremiumNumbersPromptingTests(unittest.TestCase):
    def test_prompt_gives_explicit_linkedin_extraction_guidance_and_a_populated_example(self) -> None:
        _, user_prompt = build_premium_numbers_prompts("Some email body with a phone number.")

        self.assertIn("linkedin.com/in/", user_prompt)
        self.assertIn('"linkedin_url": "linkedin.com/in/priya-recruiter"', user_prompt)
        self.assertIn("never invent one", user_prompt)


if __name__ == "__main__":
    unittest.main()
