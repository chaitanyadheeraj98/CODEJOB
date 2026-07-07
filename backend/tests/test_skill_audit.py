import unittest

from app.parsing.skill_audit import audit_skills_text, build_skills_json_payload, skill_audit_result_to_payload


class SkillAuditTests(unittest.TestCase):
    def test_audit_keeps_normal_comma_separated_skills_unchanged(self) -> None:
        result = audit_skills_text("Java, Spring Boot, Temporal Workflow")

        self.assertEqual(result.skills_text, "Java, Spring Boot, Temporal Workflow")
        self.assertEqual(result.known, ("Java", "Spring Boot"))
        self.assertEqual(result.unknown, ("Temporal Workflow",))

    def test_audit_keeps_normal_newline_separated_skills_unchanged(self) -> None:
        result = audit_skills_text("Java\nSpring Boot\nTemporal Workflow")

        self.assertEqual(result.skills_text, "Java, Spring Boot, Temporal Workflow")
        self.assertEqual(result.known, ("Java", "Spring Boot"))
        self.assertEqual(result.unknown, ("Temporal Workflow",))

    def test_audit_preserves_unknown_skills_in_final_skills_text(self) -> None:
        result = audit_skills_text("Java, Temporal Workflow, Spring Boot, Temporal Workflow")
        payload = skill_audit_result_to_payload(result)

        self.assertEqual(payload["skills_text"], "Java, Temporal Workflow, Spring Boot")
        self.assertEqual(payload["known"], ["Java", "Spring Boot"])
        self.assertEqual(payload["unknown"], ["Temporal Workflow"])
        self.assertIn("known", payload["evidence"])
        self.assertIn("unknown", payload["evidence"])

    def test_audit_returns_safe_empty_payload_for_blank_input(self) -> None:
        result = audit_skills_text("")
        payload = skill_audit_result_to_payload(result)

        self.assertEqual(payload["skills_text"], "none_detected")
        self.assertEqual(payload["known"], [])
        self.assertEqual(payload["unknown"], [])
        self.assertEqual(payload["evidence"], {})

    def test_build_skills_json_payload_prefers_existing_skills_audit(self) -> None:
        payload = build_skills_json_payload(
            {
                "skills_audit": {
                    "skills_text": "Java, Temporal Workflow",
                    "known": ["Java"],
                    "unknown": ["Temporal Workflow"],
                    "evidence": {"known": ["java -> Java"], "unknown": ["Temporal Workflow"]},
                }
            },
            fallback_skills_text="Java",
        )

        self.assertEqual(payload["skills_text"], "Java, Temporal Workflow")
        self.assertEqual(payload["known"], ["Java"])
        self.assertEqual(payload["unknown"], ["Temporal Workflow"])

    def test_build_skills_json_payload_falls_back_to_auditing_skills_text(self) -> None:
        payload = build_skills_json_payload({}, fallback_skills_text="Java, Temporal Workflow")

        self.assertEqual(payload["skills_text"], "Java, Temporal Workflow")
        self.assertEqual(payload["known"], ["Java"])
        self.assertEqual(payload["unknown"], ["Temporal Workflow"])

    def test_audit_recovers_multiple_known_skills_from_space_separated_blob(self) -> None:
        result = audit_skills_text(
            "JavaScript TypeScript Java SQL React Angular Docker Kubernetes AWS Azure Jenkins GitHub Actions"
        )

        self.assertIn("Java", result.known)
        self.assertIn("SQL", result.known)
        self.assertTrue("React" in result.known or "React.js" in result.known)
        self.assertIn("Angular", result.known)
        self.assertIn("Docker", result.known)
        self.assertIn("Kubernetes", result.known)
        self.assertEqual(result.unknown, ())
        self.assertNotIn("JavaScript TypeScript Java SQL React Angular Docker Kubernetes AWS Azure Jenkins GitHub Actions", result.skills_text)

    def test_audit_suppresses_giant_malformed_phrase_when_recovery_fails(self) -> None:
        result = audit_skills_text("florb snazzle quentor zibble marnix ploonet dravik strallop")

        self.assertEqual(result.skills_text, "none_detected")
        self.assertEqual(result.known, ())
        self.assertEqual(result.unknown, ())

    def test_audit_keeps_short_unknown_skill_candidates(self) -> None:
        result = audit_skills_text("Java, PromptForge")

        self.assertEqual(result.known, ("Java",))
        self.assertEqual(result.unknown, ("PromptForge",))


if __name__ == "__main__":
    unittest.main()
