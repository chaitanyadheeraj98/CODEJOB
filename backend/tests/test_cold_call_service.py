import unittest
from unittest.mock import patch

from app.cold_call.service import ColdCallContext, generate_cold_call_script
from app.cold_call.skill_overlap import find_allowed_cold_call_skills


class ColdCallSkillOverlapTests(unittest.TestCase):
    def test_required_overlap_skills_are_allowed(self) -> None:
        requirement_text = """
        Required Skills:
        Java
        Spring Boot
        Kubernetes

        Preferred Skills:
        React
        """
        matches = find_allowed_cold_call_skills(
            requirement_text=requirement_text,
            resume_skills_text="Java, Spring Boot, React",
            resume_text="7+ years in Java and Spring Boot with some React work.",
        )
        self.assertEqual([item.display for item in matches], ["Java", "Spring Boot"])

    def test_resume_only_skill_is_not_allowed(self) -> None:
        requirement_text = """
        Required Skills:
        Java
        Spring Boot
        """
        matches = find_allowed_cold_call_skills(
            requirement_text=requirement_text,
            resume_skills_text="Java, Spring Boot, React",
            resume_text="Strong Java, Spring Boot, and React background.",
        )
        self.assertNotIn("React", [item.display for item in matches])

    def test_requirement_only_skill_is_not_allowed(self) -> None:
        requirement_text = """
        Mandatory Skills:
        Java
        OpenShift
        """
        matches = find_allowed_cold_call_skills(
            requirement_text=requirement_text,
            resume_skills_text="Java",
            resume_text="Experienced Java engineer.",
        )
        self.assertEqual([item.display for item in matches], ["Java"])


class ColdCallServiceTests(unittest.TestCase):
    def test_caps_years_and_limits_to_four_sentences(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Senior Java Developer",
            location="Austin, TX",
            allowed_skill_highlights="Java, Spring Boot",
            allowed_skill_canonicals=("Java", "Spring Boot"),
            evidence="Need Java lead.",
        )
        resume_text = "7+ years Java and Spring experience."
        model_output = (
            "Hi this is Chaithanya calling about your role. "
            "I have 10 years of Java experience. "
            "My recent work includes Spring Boot delivery. "
            "Would you be open to a quick conversation today? "
            "I can also share additional project details."
        )
        with patch("app.cold_call.service.deepseek_chat_completion", return_value=model_output):
            script = generate_cold_call_script(context=context, resume_text=resume_text, model_name="deepseek-chat")
        self.assertNotIn("10 years", script)
        self.assertIn("7+ years", script)
        self.assertLessEqual(len([s for s in script.split(".") if s.strip()]), 4)

    def test_softens_aop_claim_if_not_in_resume(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Lead",
            location="Irving, TX",
            allowed_skill_highlights="Java, Spring Framework",
            allowed_skill_canonicals=("Java", "Spring Framework"),
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

    def test_no_overlap_uses_generic_fallback_without_tool_dump(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Full Stack Developer",
            location="Plano, TX",
            allowed_skill_highlights="",
            allowed_skill_canonicals=(),
            evidence="Hiring now",
        )
        with patch("app.cold_call.service.deepseek_chat_completion", side_effect=RuntimeError("provider down")):
            script = generate_cold_call_script(context=context, resume_text="7+ years engineering", model_name="deepseek-chat")
        self.assertIn("relevant full-stack engineering experience", script)
        self.assertNotIn("Java and Spring technologies", script)

    def test_disallowed_model_skill_uses_safe_fallback(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Full Stack Developer",
            location="Plano, TX",
            allowed_skill_highlights="Java",
            allowed_skill_canonicals=("Java",),
            evidence="Hiring now",
        )
        with patch(
            "app.cold_call.service.deepseek_chat_completion",
            return_value="Hi, I have 7+ years with Java and AWS. Can we speak today?",
        ):
            script = generate_cold_call_script(context=context, resume_text="7+ years Java", model_name="deepseek-chat")
        self.assertIn("resume-backed experience in Java", script)
        self.assertNotIn("AWS", script)

    def test_model_name_is_forwarded(self) -> None:
        context = ColdCallContext(
            recruiter_name="Recruiter",
            recruiter_email="recruiter@example.com",
            job_title="Java Full Stack Developer",
            location="Plano, TX",
            allowed_skill_highlights="Java",
            allowed_skill_canonicals=("Java",),
            evidence="Hiring now",
        )
        with patch(
            "app.cold_call.service.deepseek_chat_completion",
            return_value="Hi, I have 7+ years with Java. Could we connect?",
        ) as completion:
            generate_cold_call_script(context=context, resume_text="7+ years Java", model_name="deepseek-chat")
        completion.assert_called_once()
        self.assertEqual(completion.call_args.kwargs["model_name"], "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
