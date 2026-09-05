"""Contact extraction from a pasted requirement.

The defect this module exists to avoid is subtle and would have looked like the
feature simply not working: `strip_recruiter_footer` cuts the message at the
sign-off, and the recruiter's address lives *below* that line. Read contacts
from parsed output and every paste loses its address - not the edge case, the
common one.

So the first test here is the WhatsApp requirement that motivated the feature,
asserting the address is found beneath "Thanks & regards".

The second theme is that nothing is ever invented. A wrong recruiter address
sends a real person's resume and document numbers to a stranger, so every field
is "" when the text does not contain it.
"""

import unittest

from app.parsing.manual_contacts import ManualContacts, extract_manual_contacts

WHATSAPP_REQUIREMENT = """Job Title:  Senior Full Stack Developer
Experience: 8+ Years
Client: TECH M
Job Type: Contract -C2C
Rate/Salary: $58/Hr on C2C
Job Location:  Plano, TX (onsite)
Relocation: Yes

Kindly don't re-submit your profile to the TECH M and for this same location and position.

Thanks & regards
T Mahesh royal
US It Recruiter
Fusion Global Technologies and Solutions
Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386
"""


class ManualContactExtractionTests(unittest.TestCase):
    def test_the_address_beneath_the_sign_off_is_found(self) -> None:
        """The whole reason this module reads raw text."""
        contacts = extract_manual_contacts(WHATSAPP_REQUIREMENT)
        self.assertEqual(contacts.recruiter_email, "mahesh@fusiongts.com")

    def test_the_rest_of_the_signature_is_found_too(self) -> None:
        contacts = extract_manual_contacts(WHATSAPP_REQUIREMENT)
        self.assertEqual(contacts.sender_name, "T Mahesh royal")
        self.assertEqual(contacts.company, "Fusion Global Technologies and Solutions")
        self.assertEqual(contacts.phone, "12104856386")
        self.assertEqual(contacts.phone_display, "(210) 485-6386")

    def test_sender_identity_is_the_row_ready_form(self) -> None:
        self.assertEqual(
            extract_manual_contacts(WHATSAPP_REQUIREMENT).sender_identity,
            "T Mahesh royal <mahesh@fusiongts.com>",
        )

    # --- nothing is invented ---------------------------------------------

    def test_a_requirement_with_no_address_yields_no_address(self) -> None:
        """Not a constructed one, and not one borrowed from the company name."""
        text = WHATSAPP_REQUIREMENT.replace(
            "Email: mahesh@fusiongts.com / Contact: +1 (210) 485-6386", "Contact: +1 (210) 485-6386"
        )
        contacts = extract_manual_contacts(text)
        self.assertEqual(contacts.recruiter_email, "")
        self.assertNotIn("fusiongts", contacts.sender_identity)
        # The rest still comes through: a missing address is not a failed parse.
        self.assertEqual(contacts.sender_name, "T Mahesh royal")
        self.assertEqual(contacts.phone, "12104856386")

    def test_a_company_is_never_derived_from_the_email_domain(self) -> None:
        text = "Java role in Dallas.\n\nThanks\nPat Recruiter\nEmail: pat@acmestaffing.com\n"
        contacts = extract_manual_contacts(text)
        self.assertEqual(contacts.recruiter_email, "pat@acmestaffing.com")
        self.assertEqual(contacts.company, "")

    def test_empty_and_whitespace_text_yield_nothing(self) -> None:
        for text in ("", "   \n\n  "):
            self.assertEqual(extract_manual_contacts(text), ManualContacts())

    def test_a_requirement_with_no_signature_at_all_yields_nothing(self) -> None:
        contacts = extract_manual_contacts("Job Title: Java Developer\nLocation: Dallas, TX\n")
        self.assertEqual(contacts, ManualContacts())

    # --- choosing between candidates --------------------------------------

    def test_an_employer_domain_address_is_not_the_recruiter(self) -> None:
        """It is the user's own side of the thread - a CC target, not a recipient."""
        text = (
            "Please send profiles to submissions@myemployer.com\n\n"
            "Thanks\nPat Recruiter\nEmail: pat@acmestaffing.com\n"
        )
        contacts = extract_manual_contacts(text, employer_domains={"myemployer.com"})
        self.assertEqual(contacts.recruiter_email, "pat@acmestaffing.com")

    def test_the_signature_address_wins_over_one_in_the_body(self) -> None:
        text = (
            "Forwarded from careers@jobboard.com\n"
            "Job Title: Java Developer\n\n"
            "Thanks & regards\nPat Recruiter\nEmail: pat@acmestaffing.com\n"
        )
        self.assertEqual(extract_manual_contacts(text).recruiter_email, "pat@acmestaffing.com")

    def test_an_address_outside_any_signature_is_still_used(self) -> None:
        """No sign-off, but a real address. Better than dropping the requirement."""
        text = "Java role in Dallas. Reply to pat@acmestaffing.com if interested.\n"
        self.assertEqual(extract_manual_contacts(text).recruiter_email, "pat@acmestaffing.com")

    def test_the_last_sign_off_is_the_signature(self) -> None:
        """"Thanks for your time" mid-body must not swallow the real footer."""
        text = (
            "Thanks for reviewing this requirement.\n"
            "Job Title: Java Developer\n\n"
            "Thanks & regards\nPat Recruiter\nUS IT Recruiter\nAcme Staffing LLC\n"
            "Email: pat@acmestaffing.com\n"
        )
        contacts = extract_manual_contacts(text)
        self.assertEqual(contacts.sender_name, "Pat Recruiter")
        self.assertEqual(contacts.company, "Acme Staffing LLC")

    def test_label_and_disclaimer_lines_are_not_mistaken_for_a_name(self) -> None:
        text = (
            "Java role.\n\nThanks & regards\n"
            "Pat Recruiter\n"
            "Technical Recruiter\n"
            "Acme Staffing LLC\n"
            "LinkedIn: linkedin.com/in/pat\n"
            "Email: pat@acmestaffing.com\n"
            "This message is confidential and intended recipient only.\n"
        )
        contacts = extract_manual_contacts(text)
        self.assertEqual(contacts.sender_name, "Pat Recruiter")
        self.assertEqual(contacts.company, "Acme Staffing LLC")

    def test_an_unparseable_phone_is_dropped_rather_than_stored(self) -> None:
        text = "Java role.\n\nThanks\nPat Recruiter\nContact: 123\nEmail: pat@acme.com\n"
        contacts = extract_manual_contacts(text)
        self.assertEqual(contacts.phone, "")
        self.assertEqual(contacts.recruiter_email, "pat@acme.com")

    def test_sender_identity_falls_back_to_whichever_half_exists(self) -> None:
        self.assertEqual(
            ManualContacts(recruiter_email="pat@acme.com").sender_identity, "pat@acme.com"
        )
        self.assertEqual(ManualContacts(sender_name="Pat").sender_identity, "Pat")
        self.assertEqual(ManualContacts().sender_identity, "")


if __name__ == "__main__":
    unittest.main()
