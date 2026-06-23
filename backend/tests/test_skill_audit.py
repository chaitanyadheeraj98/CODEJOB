import unittest

from app.parsing.skill_audit import audit_skills_text, build_skills_json_payload, skill_audit_result_to_payload


class SkillAuditTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
