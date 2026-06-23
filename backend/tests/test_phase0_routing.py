import unittest

from unittest.mock import patch

from app.phase0 import (
    DEFAULT_FALLBACK_DRAFT_TEMPLATE,
    analyze_recipient_routing,
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
)


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
    def test_parse_email_with_details_preserves_contract_and_adds_metadata(self) -> None:
        parsed, details = parse_email_with_details(
            "Role: AI Engineer",
            "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG, Embeddings",
            source="gmail",
        )

        self.assertEqual(set(parsed.keys()), {"role", "location", "job_location_text", "salary_text", "skills_text", "f2f_mentioned", "asks_contact_fields", "is_texas_role"})
        self.assertEqual(details["source"], "gmail")
        self.assertIn("base_parser_result", details)
        self.assertIn("enrichment_result", details)
        self.assertIn("merged_result", details)
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertIn("Python", str(parsed["skills_text"]))

    def test_parse_email_with_details_keeps_ai_extractor_disabled_by_default(self) -> None:
        with patch("app.phase0.extract_ai_job_details") as mock_ai:
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG, Embeddings",
                source="gmail",
            )

        mock_ai.assert_not_called()
        self.assertEqual(details["parser_version"], "spacy_enrichment_v1")
        self.assertIsNone(details["ai_extractor_result"])
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertEqual(details["ai_merge_notes"], [])
        self.assertEqual(parsed["role"], "AI Engineer")

    def test_parse_email_with_details_merges_ai_skills_when_enabled(self) -> None:
        ai_result = {
            "role_candidates": ["AI Engineer"],
            "company": "",
            "primary_location": "Alpharetta, GA",
            "mentioned_locations": ["Alpharetta, GA"],
            "work_mode": "",
            "visa_hints": [],
            "experience_years_min": 2,
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
        self.assertEqual(details["parser_version"], "spacy_ai_enrichment_v2")
        self.assertEqual(details["ai_extractor_result"]["skills_unknown"], ["Temporal"])
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], ["Temporal"])
        self.assertIn("Amazon ECS", str(parsed["skills_text"]))
        self.assertIn("Grafana", str(parsed["skills_text"]))
        self.assertTrue(details["ai_merge_notes"])

    def test_parse_email_with_details_does_not_let_weak_ai_overwrite_clean_role(self) -> None:
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

        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertEqual(details["merged_result"]["role"], "AI Engineer")
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertFalse(any("role candidate" in note for note in details["ai_merge_notes"]))

    def test_parse_email_with_details_survives_ai_extractor_failure_without_contract_change(self) -> None:
        with patch("app.phase0.extract_ai_job_details", side_effect=RuntimeError("extractor timeout")):
            parsed, details = parse_email_with_details(
                "Role: AI Engineer",
                "Location: Alpharetta, GA\nRequired Qualifications:\nPython, Java, RAG",
                source="gmail",
                ai_extractor_enabled=True,
            )

        self.assertEqual(parsed["role"], "AI Engineer")
        self.assertEqual(details["parser_version"], "spacy_ai_enrichment_v2")
        self.assertEqual(details["ai_extractor_result"]["error"], "extractor timeout")
        self.assertEqual(details["approved_skills_text"], parsed["skills_text"])
        self.assertEqual(details["unknown_skills"], [])
        self.assertEqual(details["ai_merge_notes"], [])

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
