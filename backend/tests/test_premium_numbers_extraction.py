import unittest
import os

os.environ["DEBUG"] = "false"

from app.premium_numbers import extraction


class PremiumNumbersExtractionTests(unittest.TestCase):
    def test_fallback_extracts_and_normalizes_phone(self) -> None:
        leads = extraction.extract_phone_leads(
            "Uma G <uma@brightpathstaffing.com>",
            "Java role",
            "Please call me at +1 (214) 555-1212. Best regards, Uma G, Senior Recruiter",
        )
        self.assertTrue(leads)
        self.assertEqual(leads[0].phone_number_normalized, "12145551212")

    def test_dedupe_keeps_higher_confidence(self) -> None:
        low = extraction.ExtractedPhoneLead(
            phone_number_display="(214) 555-1212",
            phone_number_normalized="2145551212",
            owner_name="Unknown",
            company="Unknown",
            designation="Unknown",
            purpose="Unknown",
            confidence="low",
            contact_type="unknown",
            recruiter_relevance_score=10,
            is_recruiter_relevant=False,
            relevance_reason="none",
            source_fragment="x",
        )
        high = extraction.ExtractedPhoneLead(
            phone_number_display="+1 214 555 1212",
            phone_number_normalized="2145551212",
            owner_name="Uma",
            company="Brightpath",
            designation="Recruiter",
            purpose="Recruiter direct number",
            confidence="high",
            contact_type="recruiter_direct",
            recruiter_relevance_score=90,
            is_recruiter_relevant=True,
            relevance_reason="positive",
            source_fragment="y",
        )
        deduped = extraction.dedupe_phone_leads([low, high])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].confidence, "high")

    def test_fallback_used_when_ai_returns_empty(self) -> None:
        original_llm = extraction._llm_extract
        try:
            extraction._llm_extract = lambda _content: []
            leads = extraction.extract_phone_leads(
                "Recruiter <r@example.com>",
                "Role",
                "Reach me at (470) 313-6209 for interview coordination.",
            )
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0].phone_number_normalized, "14703136209")
        finally:
            extraction._llm_extract = original_llm

    def test_normalize_variants_to_same_canonical(self) -> None:
        lead_a = extraction.ExtractedPhoneLead(
            phone_number_display="+1 248 237 7696",
            phone_number_normalized=extraction._normalize_phone("+1 248 237 7696"),
            owner_name="A",
            company="X",
            designation="Unknown",
            purpose="Unknown",
            confidence="medium",
            contact_type="unknown",
            recruiter_relevance_score=50,
            is_recruiter_relevant=False,
            relevance_reason="none",
            source_fragment="x",
        )
        lead_b = extraction.ExtractedPhoneLead(
            phone_number_display="(248) 237-7696",
            phone_number_normalized=extraction._normalize_phone("(248) 237-7696"),
            owner_name="B",
            company="Y",
            designation="Unknown",
            purpose="Unknown",
            confidence="high",
            contact_type="unknown",
            recruiter_relevance_score=60,
            is_recruiter_relevant=False,
            relevance_reason="none",
            source_fragment="y",
        )
        self.assertEqual(lead_a.phone_number_normalized, "12482377696")
        self.assertEqual(lead_b.phone_number_normalized, "12482377696")
        deduped = extraction.dedupe_phone_leads([lead_a, lead_b])
        self.assertEqual(len(deduped), 1)

    def test_employer_domain_marks_internal_number(self) -> None:
        leads = extraction.extract_phone_leads(
            "Sheshwika <sheshwika@horizonsoftech.net>",
            "Role",
            "Please call me at +1 248 247 6165. Regards, Bench Sales Recruiter",
            employer_domains={"horizonsoftech.net"},
        )
        self.assertTrue(leads)
        self.assertFalse(leads[0].is_recruiter_relevant)
        self.assertEqual(leads[0].contact_type, "employer_internal")


if __name__ == "__main__":
    unittest.main()
