import unittest

from app.parsing.skill_audit import (
    audit_skills_text,
    build_skills_json_payload,
    custom_skill_requires_review,
    is_safe_for_bulk_skill_approval,
    skill_audit_result_to_payload,
    split_skill_tokens,
)


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
        self.assertEqual(payload["unknown_source"], "legacy")

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

    def test_build_skills_json_payload_reaudits_list_only_legacy_payload_and_records_source(self) -> None:
        payload = build_skills_json_payload(
            {
                "parser_mode": "ai_primary",
                "skills_audit": {"known": ["Java"], "unknown": ["PromptForge"]},
            },
            fallback_skills_text="Spring Boot",
        )

        self.assertEqual(payload["skills_text"], "Java, PromptForge")
        self.assertEqual(payload["known"], ["Java"])
        self.assertEqual(payload["unknown"], ["PromptForge"])
        self.assertEqual(payload["unknown_source"], "ai")

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

    def test_sentence_splitter_preserves_versions_and_splits_real_sentence_boundaries(self) -> None:
        self.assertEqual(
            split_skill_tokens("Node.js, Angular 17.0, Java 8. Spring Boot"),
            ["Node.js", "Angular 17.0", "Java 8", "Spring Boot"],
        )

    def test_real_pollution_fragments_recover_known_skills_without_unknown_leftovers(self) -> None:
        expectations = {
            "Vue.js) is a plus. Kafka": "Kafka",
            "with a focus on IAM": "IAM",
            "Windchill Customization (Java": "Java",
        }
        for raw, expected in expectations.items():
            with self.subTest(raw=raw):
                result = audit_skills_text(raw)
                self.assertIn(expected, result.known)
                self.assertEqual(result.unknown, ())

        self.assertEqual(audit_skills_text("web security (XSRF").unknown, ())

    def test_placeholders_are_filtered_and_multiword_alias_is_preserved(self) -> None:
        self.assertEqual(audit_skills_text("none_detected, unknown, n/a").unknown, ())
        result = audit_skills_text("Human in the Loop")
        self.assertEqual(result.known, ("Human-in-the-Loop",))
        self.assertEqual(result.unknown, ())

    def test_bare_numeric_tokens_are_filtered_like_placeholders(self) -> None:
        self.assertEqual(audit_skills_text("Java, 11, 17, 10+").unknown, ())

    def test_bulk_approval_accepts_only_clean_atomic_unknowns(self) -> None:
        self.assertTrue(is_safe_for_bulk_skill_approval("PromptForge"))
        for raw in (
            "Vue.js) is a plus. Kafka",
            "with a focus on IAM",
            "Windchill Customization (Java",
            "web security (XSRF",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(is_safe_for_bulk_skill_approval(raw))

    def test_cleanup_classifier_flags_pollution_without_flagging_valid_multiword_alias(self) -> None:
        self.assertTrue(custom_skill_requires_review("with a focus on IAM"))
        self.assertTrue(custom_skill_requires_review("Windchill Customization (Java"))
        self.assertFalse(custom_skill_requires_review("Human-in-the-Loop"))


if __name__ == "__main__":
    unittest.main()
