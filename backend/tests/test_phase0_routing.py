import unittest

from app.phase0 import (
    DEFAULT_FALLBACK_DRAFT_TEMPLATE,
    analyze_recipient_routing,
    draft_reply,
    email_domain,
    greeting_from_to_contact,
    render_fallback_draft_template,
    resolve_to_cc,
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
        self.assertEqual(greeting, "Hi Sudarsan,")

    def test_greeting_falls_back_to_generic_for_role_mailbox(self) -> None:
        body = "Please send your resume to jobs@yvstech.com"
        greeting = greeting_from_to_contact("jobs@yvstech.com", body)
        self.assertEqual(greeting, "Hi,")

    def test_render_fallback_template_replaces_supported_tokens(self) -> None:
        rendered = render_fallback_draft_template(
            DEFAULT_FALLBACK_DRAFT_TEMPLATE,
            {
                "greeting": "Hi Sudarsan,",
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

        self.assertIn("Hi Sudarsan,", rendered)
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
