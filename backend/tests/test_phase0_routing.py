import unittest

from unittest.mock import patch

from app.models import UserSettings
from app.phase0 import (
    DEFAULT_FALLBACK_DRAFT_TEMPLATE,
    analyze_recipient_routing,
    hard_filter_check,
    build_skill_source_sections,
    classify_section_heading,
    draft_reply,
    email_domain,
    greeting_from_to_contact,
    normalize_employer_domains,
    parse_email,
    parse_email_with_details,
    render_fallback_draft_template,
    resolve_to_cc,
    slice_jd_sections,
    should_block_f2f,
    strip_recruiter_footer,
)
from app.services import policy_service


EMAIL_30_BODY = """
Title: Java Microservices RPA Developer

Location: Plano, TX (8AM - 5PM or 9AM - 6 PM)

Thanks

Sudarsan
Cystems Logic Inc,
Ph: 818-518-9753
Email: sudarsan@cystemslogic.com
www.cystemslogic.com

Thanks & Regards
Prashanth Kinnera
Bench Sales Recruiter
Ph : (770) 824-0630
Email: Kprashanth@horizonsoftech.net
www.horizonsoftech.net
"""


class RecipientRoutingTests(unittest.TestCase):
    def test_pre_jd_share_resume_instruction_is_not_treated_as_footer(self) -> None:
        body = """Please share resumes along with your LinkedIn URL

Title: AI Pod Product Owner
Job ID: DLTJP00057258
VISA: USC/GC Only
Required Qualifications:
14+ years total experience and 10+ years US experience
"""

        self.assertEqual(strip_recruiter_footer(body), body)

    def _user_settings(self) -> UserSettings:
        return UserSettings(
            owner_id="default-owner",
            accepted_locations="texas,remote",
            min_salary=60,
            must_have_skills="java,spring",
        )

    def _parsed_candidate(self) -> dict[str, str | int | bool]:
        return {
            "role": "Java Developer",
            "location": "florida",
            "job_location_text": "unknown",
            "salary_text": "$55/hr",
            "skills_text": "java",
            "f2f_mentioned": False,
            "asks_contact_fields": False,
            "is_texas_role": False,
        }

    def test_parse_email_with_details_preserves_contract_and_adds_metadata(self) -> None:
        parsed, details = parse_email_with_details(
            "Role: AI Engineer",
            "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG, Embeddings",
            source="gmail",
        )

        self.assertEqual(set(parsed.keys()), {"role", "location", "job_location_text", "salary_text", "skills_text", "f2f_mentioned", "asks_contact_fields", "is_texas_role"})
        self.assertEqual(details["source"], "gmail")
        self.assertIn("base_parser_result", details)
        self.assertIn("merged_result", details)
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertIn("Python", str(parsed["skills_text"]))
        self.assertEqual(details["requirements_schema_version"], 1)
        self.assertIn("structured_requirements", details)
        self.assertIn("required_groups", details["structured_requirements"])

    def test_placeholder_skill_is_not_promoted_to_a_mandatory_requirement(self) -> None:
        parsed, details = parse_email_with_details(
            "Role: Project Manager",
            "Location: Remote\nRequired skills: none_detected, unknown",
            source="gmail",
        )

        self.assertEqual(parsed["skills_text"], "none_detected")
        self.assertEqual(details["structured_requirements"]["required_groups"], [])

    def test_parse_email_with_details_freezes_current_details_shape(self) -> None:
        _parsed, details = parse_email_with_details(
            "Role: AI Engineer",
            "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG, Embeddings",
            source="gmail",
        )

        self.assertEqual(
            set(details.keys()),
            {
                "parser_version",
                "parser_mode",
                "source",
                "base_parser_result",
                "ai_extractor_result",
                "approved_skills_text",
                "unknown_skills",
                "skills_audit",
                "merged_result",
                "ai_merge_notes",
                "parser_warning",
                "fallback_used",
                "source_hints",
                "ai_input_source",
                "ai_input_chars",
                "structured_requirements",
                "requirements_schema_version",
            },
        )
        self.assertEqual(details["parser_version"], "base_only_v2")
        self.assertEqual(details["parser_mode"], "base_only")
        self.assertIsNone(details["parser_warning"])
        self.assertFalse(details["fallback_used"])
        self.assertEqual(details["source_hints"], {})
        self.assertEqual(details["ai_input_source"], "")
        self.assertEqual(details["ai_input_chars"], 0)
        self.assertIsInstance(details["ai_merge_notes"], list)
        self.assertEqual(details["skills_audit"]["skills_text"], _parsed["skills_text"])
        self.assertEqual(details["requirements_schema_version"], 1)
        self.assertIsInstance(details["structured_requirements"], dict)

    def test_parse_email_with_details_keeps_ai_extractor_disabled_by_default(self) -> None:
        with patch("app.phase0.extract_ai_job_details") as mock_ai:
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG, Embeddings",
                source="gmail",
            )

        mock_ai.assert_not_called()
        self.assertEqual(details["parser_version"], "base_only_v2")
        self.assertEqual(details["parser_mode"], "base_only")
        self.assertIsNone(details["ai_extractor_result"])
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertEqual(details["ai_merge_notes"], [])
        self.assertIsNone(details["parser_warning"])
        self.assertFalse(details["fallback_used"])
        self.assertEqual(details["ai_input_source"], "")
        self.assertEqual(details["ai_input_chars"], 0)
        self.assertEqual(parsed["role"], "AI Engineer")

    def test_parse_email_with_details_uses_ai_primary_result_when_enabled(self) -> None:
        ai_result = {
            "role_candidates": ["AI Engineer"],
            "company": "",
            "primary_location": "Alpharetta, GA",
            "mentioned_locations": ["Alpharetta, GA"],
            "work_mode": "",
            "visa_hints": [],
            "experience_years_min": 2,
            "salary_text": "$95/hr",
            "skills_text": "Amazon ECS, Grafana, Temporal",
            "must_have_skills": ["Amazon ECS"],
            "nice_to_have_skills": ["Grafana"],
            "f2f_mentioned": False,
            "asks_contact_fields": True,
            "is_texas_role": False,
            "skills_approved": ["Amazon ECS", "Grafana"],
            "skills_unknown": ["Temporal"],
            "confidence": 0.82,
            "evidence": {"skills": ["Amazon ECS, Grafana, Temporal"]},
        }

        with patch("app.phase0.extract_ai_job_details") as mock_ai, patch(
            "app.phase0.ai_extractor_result_to_payload",
            return_value=ai_result,
        ):
            mock_ai.return_value = type(
                "AIResult",
                (),
                {
                    "role_candidates": ("AI Engineer",),
                    "company": "",
                    "primary_location": "Alpharetta, GA",
                    "mentioned_locations": ("Alpharetta, GA",),
                    "work_mode": "",
                    "visa_hints": (),
                    "experience_years_min": 2,
                    "skills_text": "Amazon ECS, Grafana, Temporal",
                    "skills_approved": ("Amazon ECS", "Grafana"),
                    "skills_unknown": ("Temporal",),
                    "confidence": 0.82,
                    "evidence": {"skills": ["Amazon ECS, Grafana, Temporal"]},
                    "error": None,
                },
            )()
            parsed, details = parse_email_with_details(
                "Unknown Role",
                "Role: AI platform\nRequired Qualifications:\nJava\nObservability",
                source="gmail",
                ai_extractor_enabled=True,
            )

        mock_ai.assert_called_once()
        self.assertEqual(details["parser_version"], "ai_primary_v2")
        self.assertEqual(details["parser_mode"], "ai_primary")
        self.assertEqual(details["ai_extractor_result"]["skills_unknown"], ["Temporal"])
        self.assertIn("Amazon ECS", details["approved_skills_text"])
        self.assertIn("Grafana", details["approved_skills_text"])
        self.assertEqual(details["unknown_skills"], ["Temporal"])
        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertEqual(parsed["location"], "Alpharetta, GA")
        self.assertEqual(parsed["salary_text"], "$95/hr")
        self.assertTrue(bool(parsed["asks_contact_fields"]))
        self.assertIn("Amazon ECS", str(parsed["skills_text"]))
        self.assertIn("Grafana", str(parsed["skills_text"]))
        self.assertIn("Temporal", str(parsed["skills_text"]))
        self.assertEqual(details["ai_merge_notes"], [])
        self.assertIsNone(details["parser_warning"])
        self.assertFalse(details["fallback_used"])
        self.assertEqual(details["skills_audit"]["unknown"], ["Temporal"])
        structured = details["structured_requirements"]
        self.assertEqual(structured["required_groups"][0]["skills"][0]["canonical_name"], "Amazon ECS")
        self.assertEqual(structured["preferred_groups"][0]["skills"][0]["canonical_name"], "Grafana")

    def test_parse_email_with_details_uses_ai_body_override_only_for_ai_input(self) -> None:
        ai_override = (
            "Python, Kubernetes, Terraform, AWS, observability, platform engineering, "
            "container orchestration, infrastructure automation"
        )
        with patch("app.phase0.extract_ai_job_details") as mock_ai, patch(
            "app.phase0.ai_extractor_result_to_payload",
            return_value={
                "role_candidates": ["Cloud Engineer"],
                "company": "",
                "primary_location": "Dallas, TX",
                "mentioned_locations": ["Dallas, TX"],
                "work_mode": "",
                "visa_hints": [],
                "experience_years_min": 5,
                "salary_text": "",
                "skills_text": "Python, Kubernetes, Terraform, AWS",
                "f2f_mentioned": False,
                "asks_contact_fields": False,
                "is_texas_role": True,
                "skills_approved": ["Python", "Kubernetes", "Terraform", "AWS"],
                "skills_unknown": [],
                "confidence": 0.88,
                "evidence": {"skills": ["Python, Kubernetes, Terraform, AWS"]},
            },
        ):
            mock_ai.return_value = type(
                "AIResult",
                (),
                {
                    "role_candidates": ("Cloud Engineer",),
                    "company": "",
                    "primary_location": "Dallas, TX",
                    "mentioned_locations": ("Dallas, TX",),
                    "work_mode": "",
                    "visa_hints": (),
                    "experience_years_min": 5,
                    "skills_text": "Python, Kubernetes, Terraform, AWS",
                    "skills_approved": ("Python", "Kubernetes", "Terraform", "AWS"),
                    "skills_unknown": (),
                    "confidence": 0.88,
                    "evidence": {"skills": ["Python, Kubernetes, Terraform, AWS"]},
                    "error": None,
                },
            )()
            parsed, details = parse_email_with_details(
                "Role: Base Engineer",
                "Role: Base Engineer\nRequired Qualifications:\nJava\nFooter noise here",
                source="nvoids",
                ai_extractor_enabled=True,
                ai_body_override=ai_override,
                source_hints={"ai_input_source": "nvoids_detail_table_row_3", "ai_input_chars": len(ai_override)},
            )

        mock_ai.assert_called_once_with(
            "Role: Base Engineer",
            ai_override,
            source="nvoids",
            source_hints={"ai_input_source": "nvoids_detail_table_row_3", "ai_input_chars": len(ai_override)},
        )
        self.assertEqual(details["ai_input_source"], "nvoids_detail_table_row_3")
        self.assertEqual(details["ai_input_chars"], len(ai_override))
        self.assertEqual(parsed["role"], "Cloud Engineer")
        self.assertEqual(details["base_parser_result"]["role"], "Base Engineer")

    def test_parse_email_with_details_uses_ai_role_when_ai_mode_is_enabled(self) -> None:
        with patch("app.phase0.extract_ai_job_details") as mock_ai, patch(
            "app.phase0.ai_extractor_result_to_payload",
            return_value={
                "role_candidates": ["Software Engineer"],
                "company": "",
                "primary_location": "",
                "mentioned_locations": [],
                "work_mode": "",
                "visa_hints": [],
                "experience_years_min": None,
                "salary_text": "not_specified",
                "skills_text": "Java",
                "f2f_mentioned": False,
                "asks_contact_fields": False,
                "is_texas_role": False,
                "skills_approved": ["Java"],
                "skills_unknown": [],
                "confidence": 0.2,
                "evidence": {},
            },
        ):
            mock_ai.return_value = type(
                "AIResult",
                (),
                {
                    "role_candidates": ("Software Engineer",),
                    "company": "",
                    "primary_location": "",
                    "mentioned_locations": (),
                    "work_mode": "",
                    "visa_hints": (),
                    "experience_years_min": None,
                    "skills_text": "Java",
                    "skills_approved": ("Java",),
                    "skills_unknown": (),
                    "confidence": 0.2,
                    "evidence": {},
                    "error": None,
                },
            )()
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG",
                source="gmail",
                ai_extractor_enabled=True,
            )

        self.assertEqual(parsed["role"], "Software Engineer")
        self.assertEqual(details["merged_result"]["role"], "Software Engineer")
        self.assertEqual(details["parser_mode"], "ai_primary")
        self.assertIsNone(details["parser_warning"])
        self.assertFalse(details["fallback_used"])

    def test_parse_email_with_details_falls_back_when_extractor_returns_error_payload(self) -> None:
        ai_override = (
            "Python, Java, RAG, embeddings, retrieval pipelines, prompt evaluation, "
            "observability, semantic search"
        )
        with patch("app.phase0.extract_ai_job_details") as mock_ai:
            mock_ai.return_value = type(
                "AIResult",
                (),
                {
                    "role_candidates": (),
                    "company": "",
                    "primary_location": "",
                    "mentioned_locations": (),
                    "work_mode": "",
                    "visa_hints": (),
                    "experience_years_min": None,
                    "skills_text": "",
                    "skills_approved": (),
                    "skills_unknown": (),
                    "confidence": 0.0,
                    "evidence": {"extractor_error": ["truncated JSON content"]},
                    "error": "truncated JSON content",
                },
            )()
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG",
                source="nvoids",
                ai_extractor_enabled=True,
                ai_body_override=ai_override,
                source_hints={
                    "canonical_title": "AI Engineer",
                    "ai_input_source": "nvoids_detail_table_row_3",
                    "ai_input_chars": len(ai_override),
                },
            )

        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertIn("Python", parsed["skills_text"])
        self.assertEqual(details["parser_version"], "ai_fallback_v2")
        self.assertEqual(details["parser_mode"], "ai_fallback")
        self.assertTrue(details["fallback_used"])
        self.assertEqual(details["ai_extractor_result"]["error"], "truncated JSON content")
        self.assertEqual(details["source_hints"]["canonical_title"], "AI Engineer")
        self.assertEqual(details["ai_input_source"], "nvoids_detail_table_row_3")
        self.assertEqual(details["ai_input_chars"], len(ai_override))
        self.assertEqual(
            details["parser_warning"],
            "AI extractor failed; base parser fallback used: truncated JSON content",
        )

    def test_policy_normalization_backfills_missing_draft_rules(self) -> None:
        normalized = policy_service.normalize_policy(
            {
                "version": 1,
                "qualification": {
                    "location_strictness": "balanced",
                    "score_threshold_override_enabled": False,
                    "score_threshold_override_value": 0.6,
                },
            }
        )
        rules = normalized["qualification"]["draft_rules"]
        self.assertEqual(normalized["version"], 2)
        self.assertEqual(rules["accepted_location"]["mode"], "block")
        self.assertEqual(rules["minimum_salary"]["mode"], "block")
        self.assertEqual(rules["must_have_skills"]["mode"], "block")
        self.assertEqual(rules["score_threshold"]["mode"], "block")
        self.assertEqual(rules["recipient_mapping"]["mode"], "block")

    def test_policy_normalization_migrates_legacy_draft_filters_to_warn(self) -> None:
        normalized = policy_service.normalize_policy(
            {
                "version": 1,
                "qualification": {
                    "draft_filters": {
                        "accepted_location_filter_enabled": False,
                        "minimum_salary_filter_enabled": False,
                        "must_have_skills_filter_enabled": False,
                    }
                },
            }
        )

        rules = normalized["qualification"]["draft_rules"]
        self.assertEqual(rules["accepted_location"]["mode"], "warn")
        self.assertEqual(rules["minimum_salary"]["mode"], "warn")
        self.assertEqual(rules["must_have_skills"]["mode"], "warn")

    def test_hard_filter_check_respects_location_rule_mode(self) -> None:
        settings = self._user_settings()
        parsed = self._parsed_candidate()
        enabled_policy = policy_service.default_policy()
        disabled_policy = policy_service.default_policy()
        disabled_policy["qualification"]["draft_rules"]["accepted_location"]["mode"] = "warn"

        self.assertEqual(
            hard_filter_check(parsed, settings, enabled_policy),
            (False, "blocked: location_mismatch, salary_below_min, missing_skills:spring"),
        )
        self.assertEqual(
            hard_filter_check(parsed, settings, disabled_policy),
            (False, "blocked: salary_below_min, missing_skills:spring"),
        )

    def test_hard_filter_check_respects_salary_and_skills_rule_modes(self) -> None:
        settings = self._user_settings()
        parsed = self._parsed_candidate()
        policy = policy_service.default_policy()
        policy["qualification"]["draft_rules"]["minimum_salary"]["mode"] = "warn"
        policy["qualification"]["draft_rules"]["must_have_skills"]["mode"] = "warn"

        self.assertEqual(
            hard_filter_check(parsed, settings, policy),
            (False, "blocked: location_mismatch"),
        )

    def test_hard_filter_check_returns_warnings_when_rules_warn_instead_of_block(self) -> None:
        settings = self._user_settings()
        parsed = self._parsed_candidate()
        policy = policy_service.default_policy()
        policy["qualification"]["draft_rules"]["accepted_location"]["mode"] = "warn"
        policy["qualification"]["draft_rules"]["minimum_salary"]["mode"] = "warn"
        policy["qualification"]["draft_rules"]["must_have_skills"]["mode"] = "warn"

        self.assertEqual(
            hard_filter_check(parsed, settings, policy),
            (True, "warnings: location_mismatch, salary_below_min, missing_skills:spring"),
        )

    def test_hard_filter_check_uses_structured_requirements_for_canonical_must_have_skills(self) -> None:
        settings = self._user_settings()
        parsed = self._parsed_candidate()
        parsed["skills_text"] = "java"
        parser_details = {
            "structured_requirements": {
                "schema_version": 1,
                "required_groups": [
                    {
                        "group_id": "spring-required",
                        "level": "mandatory",
                        "mode": "all",
                        "skills": [
                            {"skill_id": "spring_boot", "canonical_name": "Spring Boot", "matched_alias": "spring boot", "evidence_text": "Spring Boot", "versions": [], "qualifiers": []},
                        ],
                        "evidence_text": "Spring Boot",
                        "section_heading": "Required Skills",
                        "section_bucket": "required",
                    }
                ],
                "preferred_groups": [],
                "informational_groups": [],
                "experience_years_min": None,
                "local_required": False,
                "work_mode": None,
                "locations": [],
                "warnings": [],
                "preferred_domains": [],
            }
        }
        policy = policy_service.default_policy()
        policy["qualification"]["draft_rules"]["accepted_location"]["mode"] = "ignore"
        policy["qualification"]["draft_rules"]["minimum_salary"]["mode"] = "ignore"
        policy["qualification"]["draft_rules"]["must_have_skills"]["skills"] = ["spring boot"]

        self.assertEqual(
            hard_filter_check(parsed, settings, policy, parser_details),
            (True, "hard_filters_passed"),
        )

    def test_hard_filter_check_uses_structured_location_when_flat_location_is_weaker(self) -> None:
        settings = self._user_settings()
        parsed = self._parsed_candidate()
        parser_details = {
            "structured_requirements": {
                "schema_version": 1,
                "required_groups": [],
                "preferred_groups": [],
                "informational_groups": [],
                "experience_years_min": 8,
                "local_required": True,
                "work_mode": "Remote",
                "locations": ["Austin, TX"],
                "warnings": [],
                "preferred_domains": [],
            }
        }
        policy = policy_service.default_policy()
        policy["qualification"]["draft_rules"]["minimum_salary"]["mode"] = "warn"
        policy["qualification"]["draft_rules"]["must_have_skills"]["mode"] = "warn"

        self.assertEqual(
            hard_filter_check(parsed, settings, policy, parser_details),
            (True, "warnings: salary_below_min, missing_skills:spring"),
        )

    def test_parse_email_with_details_survives_ai_extractor_failure_without_contract_change(self) -> None:
        with patch("app.phase0.extract_ai_job_details", side_effect=RuntimeError("extractor timeout")):
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG",
                source="gmail",
                ai_extractor_enabled=True,
            )

        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertEqual(details["parser_version"], "ai_fallback_v2")
        self.assertEqual(details["parser_mode"], "ai_fallback")
        self.assertEqual(details["ai_extractor_result"]["error"], "extractor timeout")
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertTrue(details["fallback_used"])
        self.assertIn("base parser fallback used", str(details["parser_warning"]))
        self.assertTrue(details["ai_merge_notes"])

    def test_classify_section_heading_maps_project_headings(self) -> None:
        self.assertEqual(classify_section_heading("Role Summary")[0], "summary")
        self.assertEqual(classify_section_heading("Required Qualifications")[0], "required")
        self.assertEqual(classify_section_heading("Preferred Qualifications")[0], "preferred")
        self.assertEqual(classify_section_heading("Technical Skills")[0], "technical_skills")
        self.assertEqual(classify_section_heading("Domain Skill")[0], "domain")
        self.assertEqual(classify_section_heading("What Success Looks Like")[0], "ai_compliance")
        self.assertEqual(classify_section_heading("Compliance & Responsible AI Expectations")[0], "ai_compliance")

    def test_slice_jd_sections_splits_multiline_jd_by_known_headings(self) -> None:
        body = """
Role Summary:
Build AI-assisted tooling.
Required Qualifications:
Python, Java, RAG
Preferred Qualifications:
Observability
Technical Skills:
REST APIs
Domain Skill:
Payments
What Success Looks Like:
Production readiness
"""

        sections = slice_jd_sections(body)
        buckets = [section.bucket for section in sections]

        self.assertIn("summary", buckets)
        self.assertIn("required", buckets)
        self.assertIn("preferred", buckets)
        self.assertIn("technical_skills", buckets)
        self.assertIn("domain", buckets)
        self.assertIn("ai_compliance", buckets)

    def test_slice_jd_sections_classifies_inline_hard_filters(self) -> None:
        body = """
Location: Alpharetta, GA
Visa: H1B
Duration: Long term
Rate: $70/hr
Required Qualifications:
Python, Java
"""

        sections = slice_jd_sections(body)
        hard_filter_headings = [section.heading for section in sections if section.bucket == "hard_filter"]

        self.assertIn("Location", hard_filter_headings)
        self.assertIn("Visa", hard_filter_headings)
        self.assertIn("Duration", hard_filter_headings)
        self.assertIn("Rate", hard_filter_headings)

        skill_sections = build_skill_source_sections(sections)
        self.assertTrue(all(section.bucket != "hard_filter" for section in skill_sections))

    def test_slice_jd_sections_adds_conservative_synthetic_requirements(self) -> None:
        body = """
Must have Banking domain experience
Strong proficiency in Java 21
This team supports enterprise systems.
"""

        sections = slice_jd_sections(body)
        self.assertEqual([section.bucket for section in sections], ["mandatory", "technical_skills"])
        self.assertEqual(sections[0].text, "Must have Banking domain experience")
        self.assertEqual(sections[1].text, "Strong proficiency in Java 21")

    def test_slice_jd_sections_does_not_promote_regular_prose_to_heading(self) -> None:
        body = """
This role builds onboarding systems and secure operational tooling.
The candidate should collaborate closely with platform partners.
"""

        sections = slice_jd_sections(body)

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].bucket, "unknown")
        self.assertIn("secure operational tooling", sections[0].text)

    def test_explicit_non_texas_interview_phrases_are_blocked(self) -> None:
        phrases = [
            "Onsite interview required",
            "In-person interview",
            "In person interview mandatory",
            "Local onsite interview",
            "Interview must be onsite",
            "Client round onsite",
        ]

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                parsed = parse_email(
                    "Lead Java Developer | O'Fallon, MO (hybrid)",
                    f"Location: O'Fallon, MO\n{phrase}\nJava Spring Boot Kafka",
                )
                blocked, reason = should_block_f2f(parsed)
                self.assertTrue(bool(parsed["f2f_mentioned"]))
                self.assertTrue(blocked)
                self.assertIn("non-Texas", reason)

    def test_explicit_texas_interview_phrase_is_not_blocked(self) -> None:
        parsed = parse_email(
            "Lead Java Developer | Plano, TX (hybrid)",
            "Location: Plano, TX\nOnsite interview required\nJava Spring Boot Kafka",
        )

        blocked, reason = should_block_f2f(parsed)

        self.assertTrue(bool(parsed["f2f_mentioned"]))
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_generic_onsite_or_hybrid_without_interview_phrase_is_not_blocked(self) -> None:
        parsed = parse_email(
            "Lead Java Developer | Cincinnati, OH (Onsite)",
            "Location: Cincinnati, OH (Onsite)\nHybrid role available\nJava Spring Boot Kafka",
        )

        blocked, reason = should_block_f2f(parsed)

        self.assertFalse(bool(parsed["f2f_mentioned"]))
        self.assertFalse(blocked)
        self.assertEqual(reason, "")

    def test_existing_face_to_face_variants_remain_blocked(self) -> None:
        for phrase in ["Face-to-Face interview", "Face to face interview", "F2F interview"]:
            with self.subTest(phrase=phrase):
                parsed = parse_email(
                    "Lead Java Developer | Columbus, OH",
                    f"Location: Columbus, OH\n{phrase}\nJava Spring Boot Kafka",
                )
                blocked, reason = should_block_f2f(parsed)
                self.assertTrue(bool(parsed["f2f_mentioned"]))
                self.assertTrue(blocked)
                self.assertIn("Columbus, OH", reason)

    def test_email_30_routes_to_body_recruiter_and_sender_employer(self) -> None:
        sender = "Prashanth Kinnera <kprashanth@horizonsoftech.net>"

        to_email, cc_email = resolve_to_cc(sender, "Java Microservices RPA Developer", EMAIL_30_BODY)

        self.assertEqual(to_email, "sudarsan@cystemslogic.com")
        self.assertEqual(cc_email, "kprashanth@horizonsoftech.net")

    def test_learned_mapping_does_not_apply_without_current_evidence(self) -> None:
        sender = "Prashanth Kinnera <kprashanth@horizonsoftech.net>"

        routing = analyze_recipient_routing(
            sender,
            "Java Microservices RPA Developer",
            EMAIL_30_BODY,
            learned_pairs=[("sujatha@algebrait.com", "harshitha@horizonsoftech.net")],
        )

        self.assertEqual(routing.to_email, "sudarsan@cystemslogic.com")
        self.assertEqual(routing.cc_email, "kprashanth@horizonsoftech.net")
        self.assertEqual(routing.status, "safe")

    def test_email_domain_extracts_from_display_name(self) -> None:
        self.assertEqual(email_domain("Prashanth Kinnera <kprashanth@horizonsoftech.net>"), "horizonsoftech.net")

    def test_employer_domains_can_be_overridden(self) -> None:
        sender = "Prashanth Kinnera <kprashanth@horizonsoftech.net>"
        routing = analyze_recipient_routing(
            sender,
            "Java Microservices RPA Developer",
            EMAIL_30_BODY,
            employer_domains=["cystemslogic.com"],
        )
        self.assertEqual(routing.to_email, "kprashanth@horizonsoftech.net")
        self.assertEqual(routing.cc_email, "sudarsan@cystemslogic.com")

    def test_normalize_employer_domains_defaults_and_normalizes(self) -> None:
        self.assertEqual(
            normalize_employer_domains(["  HorizonSoftTech.Net  ", "horizonsoftech.net", ""]),
            {"horizonsofttech.net", "horizonsoftech.net"},
        )
        fallback = normalize_employer_domains([])
        self.assertIn("horizonsofttech.net", fallback)

    def test_draft_reply_uses_plain_text_skill_labels(self) -> None:
        draft = draft_reply(
            "Recruiter <recruiter@example.com>",
            "Java Developer",
            {
                "skills_text": "java, spring, spring boot, microservices, kafka, aws",
                "asks_contact_fields": True,
            },
        )

        self.assertIn("- **Java**", draft)
        self.assertIn("Best regards,", draft)
        self.assertIn("📞 +1 940-629-6920", draft)

    def test_greeting_uses_name_from_to_contact_evidence(self) -> None:
        body = """
Thanks,
Sudarsan
Email: sudarsan@cystemslogic.com
"""
        greeting = greeting_from_to_contact("sudarsan@cystemslogic.com", body)
        self.assertEqual(greeting, "Dear Recruiter,")

    def test_greeting_falls_back_to_generic_for_role_mailbox(self) -> None:
        body = "Please send your resume to jobs@yvstech.com"
        greeting = greeting_from_to_contact("jobs@yvstech.com", body)
        self.assertEqual(greeting, "Dear Recruiter,")

    def test_render_fallback_template_replaces_supported_tokens(self) -> None:
        rendered = render_fallback_draft_template(
            DEFAULT_FALLBACK_DRAFT_TEMPLATE,
            {
                "greeting": "Dear Recruiter,",
                "role": "Java Developer",
                "sender": "Recruiter <recruiter@example.com>",
                "location": "TX",
                "salary_text": "$80/hr",
                "skills_list": "- Java\n- AWS",
                "skills_inline": "Java, AWS",
                "resume_file_name": "resume.pdf",
                "signature_name": "Jane Doe",
                "signature_phone": "+1 555-555-5555",
                "signature_email": "jane@example.com",
                "requested_details_block": "Requested details:\n- Visa: H1B",
            },
        )

        self.assertIn("Dear Recruiter,", rendered)
        self.assertIn("Application for Java Developer", rendered)
        self.assertIn("- Java", rendered)
        self.assertIn("Jane Doe", rendered)

    def test_render_fallback_template_leaves_unknown_tokens(self) -> None:
        rendered = render_fallback_draft_template(
            "Hello {{role}} {{unknown_token}}",
            {"role": "Developer"},
        )
        self.assertEqual(rendered, "Hello Developer {{unknown_token}}")


if __name__ == "__main__":
    unittest.main()
