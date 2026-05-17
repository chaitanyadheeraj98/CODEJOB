import unittest
from unittest.mock import patch

from app.cold_call.service import ColdCallContext, generate_cold_call_script


class ColdCallServiceTests(unittest.TestCase):
    def test_caps_years_and_limits_to_five_sentences(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Senior Java Developer",
            location="Austin, TX",
            skills="java, spring boot, sql",
            evidence="Need Java lead.",
        )
        resume_text = "7+ years Java and Spring experience."
        model_output = (
            "Hi this is Chaithanya calling about your role. "
            "I have 10 years of Java experience. "
            "I work in full-stack development at Centier Bank. "
            "My background matches Spring and SQL requirements. "
            "Would you be open to a quick conversation today? "
            "I can also share additional project details."
        )
        with patch("app.cold_call.service.deepseek_chat_completion", return_value=model_output):
            script = generate_cold_call_script(context=context, resume_text=resume_text, model_name="deepseek-chat")
        self.assertNotIn("10 years", script)
        self.assertIn("7+ years", script)
        self.assertLessEqual(len([s for s in script.split(".") if s.strip()]), 5)

    def test_softens_aop_claim_if_not_in_resume(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Lead",
            location="Irving, TX",
            skills="java, spring",
            evidence="AOP mentioned in JD",
        )
        resume_text = "7+ years Java, Spring Boot, SQL."
        with patch(
            "app.cold_call.service.deepseek_chat_completion",
            return_value="I have strong Spring AOP experience and can discuss details.",
        ):
            script = generate_cold_call_script(context=context, resume_text=resume_text, model_name="deepseek-chat")
        self.assertNotIn("Spring AOP", script)
        self.assertIn("Spring-based transaction management and security implementations", script)

    def test_fallback_used_if_model_errors(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Full Stack Developer",
            location="Plano, TX",
            skills="java, spring boot, kafka",
            evidence="Hiring now",
        )
        with patch("app.cold_call.service.deepseek_chat_completion", side_effect=RuntimeError("provider down")):
            script = generate_cold_call_script(context=context, resume_text="7+ years Java", model_name="deepseek-chat")
        self.assertTrue(script)
        self.assertIn("Chaithanya Dheeraj", script)


if __name__ == "__main__":
    unittest.main()
