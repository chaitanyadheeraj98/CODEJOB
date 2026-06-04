import unittest
import os
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from app.premium_numbers import extraction
from app.premium_numbers.phone_normalization import format_phone


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
            contact_email="",
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
            contact_email="uma@example.com",
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
            contact_email="a@example.com",
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
            contact_email="b@example.com",
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

    def test_phone_formatter_standardizes_display_and_extension(self) -> None:
        normalized, display, ext = format_phone("Phone: (972) - 756 - 1212 Ext 128")
        self.assertEqual(normalized, "19727561212")
        self.assertEqual(display, "(972) 756-1212 ext 128")
        self.assertEqual(ext, "128")

    def test_phone_formatter_standardizes_star_extension(self) -> None:
        normalized, display, ext = format_phone("(609) 888 6198 * 113")
        self.assertEqual(normalized, "16098886198")
        self.assertEqual(display, "(609) 888-6198 ext 113")
        self.assertEqual(ext, "113")

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

    def test_extract_owner_name_from_contact_email_line(self) -> None:
        leads = extraction.extract_phone_leads(
            "Samshritha <samshritha@horizonsoftech.net>",
            "Senior Talend Developer",
            "please share the suitable resume to Rabbanis@kgatetech.com - +1 832-271-3861",
        )
        self.assertTrue(leads)
        self.assertEqual(leads[0].phone_number_normalized, "18322713861")
        self.assertEqual(leads[0].owner_name, "Rabbanis")

    def test_extract_owner_name_prefers_target_contact_over_signature_name(self) -> None:
        leads = extraction.extract_phone_leads(
            "Samshritha Gangula <samshritha@horizonsoftech.net>",
            "Senior Talend Developer",
            (
                "please share the suitable resume to Rabbanis@kgatetech.com - +1 832-271-3861\n"
                "Thanks & Regards\n"
                "Samshritha Gangula\n"
                "Bench Sales Recruiter"
            ),
        )
        self.assertTrue(leads)
        target = [lead for lead in leads if lead.phone_number_normalized == "18322713861"]
        self.assertTrue(target)
        self.assertEqual(target[0].owner_name, "Rabbanis")

    def test_extract_owner_name_when_to_and_email_are_split_by_newline(self) -> None:
        leads = extraction.extract_phone_leads(
            "Samshritha Gangula <samshritha@horizonsoftech.net>",
            "Senior Talend Developer",
            (
                "please share the suitable resume to \n"
                "<mailto:Rabbanis@kgatetech.com> Rabbanis@kgatetech.com - +1 832-271-3861\n"
                "Thanks & Regards\n"
                "Samshritha Gangula\n"
                "Bench Sales Recruiter"
            ),
        )
        target = [lead for lead in leads if lead.phone_number_normalized == "18322713861"]
        self.assertTrue(target)
        self.assertEqual(target[0].owner_name, "Rabbanis")

    def test_dedupe_prefers_non_unknown_owner_on_confidence_tie(self) -> None:
        unknown = extraction.ExtractedPhoneLead(
            phone_number_display="+1 832-271-3861",
            phone_number_normalized="18322713861",
            owner_name="Unknown",
            contact_email="",
            company="Unknown",
            designation="Unknown",
            purpose="Signature block phone number",
            confidence="high",
            contact_type="unknown",
            recruiter_relevance_score=0,
            is_recruiter_relevant=False,
            relevance_reason="none",
            source_fragment="x",
        )
        named = extraction.ExtractedPhoneLead(
            phone_number_display="+1 832-271-3861",
            phone_number_normalized="18322713861",
            owner_name="Rabbanis",
            contact_email="rabbanis@kgatetech.com",
            company="Kgatetech",
            designation="Unknown",
            purpose="Recruiter direct number",
            confidence="high",
            contact_type="recruiter_direct",
            recruiter_relevance_score=75,
            is_recruiter_relevant=True,
            relevance_reason="external_domain,cta_context",
            source_fragment="y",
        )
        deduped = extraction.dedupe_phone_leads([unknown, named])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].owner_name, "Rabbanis")

    def test_fallback_ignores_groups_msgid_footer_digits(self) -> None:
        original_llm = extraction._llm_extract
        try:
            extraction._llm_extract = lambda _content: []
            body = (
                "To unsubscribe from this group and stop receiving emails from it, send an email to "
                "hstjava+unsubscribe@googlegroups.com. "
                "To view this discussion visit "
                "https://groups.google.com/d/msgid/hstjava/01b001dceedd%2482913610%2487b3a230%24%40horizonsoftech.net"
            )
            leads = extraction.extract_phone_leads(
                "Horizon Team <jobs@horizonsoftech.net>",
                "Java Full stack Developer",
                body,
                employer_domains={"horizonsoftech.net"},
            )
            self.assertEqual(leads, [])
        finally:
            extraction._llm_extract = original_llm

    def test_fallback_ignores_mailto_unsubscribe_numeric_noise(self) -> None:
        original_llm = extraction._llm_extract
        try:
            extraction._llm_extract = lambda _content: []
            body = (
                "Footer: <mailto:hstjava+unsubscribe@googlegroups.com> "
                "Reference token 2482913610 in mailing metadata only."
            )
            leads = extraction.extract_phone_leads(
                "Horizon Team <jobs@horizonsoftech.net>",
                "Java Update",
                body,
                employer_domains={"horizonsoftech.net"},
            )
            self.assertEqual(leads, [])
        finally:
            extraction._llm_extract = original_llm

    def test_fallback_keeps_real_phone_with_contact_intent(self) -> None:
        original_llm = extraction._llm_extract
        try:
            extraction._llm_extract = lambda _content: []
            leads = extraction.extract_phone_leads(
                "Uma G <uma@brightpathstaffing.com>",
                "Java role",
                "Call me at +1 (214) 555-1212 for details.",
            )
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0].phone_number_normalized, "12145551212")
        finally:
            extraction._llm_extract = original_llm

    def test_sbert_stage_keeps_candidate_when_margin_is_positive(self) -> None:
        original_llm = extraction._llm_extract
        original_sbert = extraction._sbert_embedding
        try:
            extraction._llm_extract = lambda _content: []
            extraction._sbert_prototype_centroids.cache_clear()

            def fake_sbert(text: str, _model: str):
                low = text.lower()
                if "call me at this number" in low or "reach me on phone" in low or "contact recruiter directly" in low or "thanks and regards recruiter signature phone" in low:
                    return [1.0, 0.0], "sbert"
                if "unsubscribe from this group" in low or "view this discussion on groups dot google dot com" in low or "tracking link with utm parameters" in low or "system footer link metadata" in low:
                    return [0.0, 1.0], "sbert"
                return [0.9, 0.1], "sbert"

            extraction._sbert_embedding = fake_sbert
            leads = extraction.extract_phone_leads(
                "Uma G <uma@brightpathstaffing.com>",
                "Java role",
                "Call me at +1 (214) 555-1212. Thanks, recruiter.",
            )
            self.assertEqual(len(leads), 1)
            self.assertIn("sbert_margin=", leads[0].relevance_reason)
        finally:
            extraction._llm_extract = original_llm
            extraction._sbert_embedding = original_sbert
            extraction._sbert_prototype_centroids.cache_clear()

    def test_sbert_stage_drops_candidate_when_margin_is_negative(self) -> None:
        original_llm = extraction._llm_extract
        original_sbert = extraction._sbert_embedding
        try:
            extraction._llm_extract = lambda _content: []
            extraction._sbert_prototype_centroids.cache_clear()

            def fake_sbert(text: str, _model: str):
                low = text.lower()
                if "call me at this number" in low or "reach me on phone" in low or "contact recruiter directly" in low or "thanks and regards recruiter signature phone" in low:
                    return [1.0, 0.0], "sbert"
                if "unsubscribe from this group" in low or "view this discussion on groups dot google dot com" in low or "tracking link with utm parameters" in low or "system footer link metadata" in low:
                    return [0.0, 1.0], "sbert"
                return [0.0, 1.0], "sbert"

            extraction._sbert_embedding = fake_sbert
            leads = extraction.extract_phone_leads(
                "Recruiter <r@example.com>",
                "Role",
                "Please phone 214-555-1212 for details.",
            )
            self.assertEqual(leads, [])
        finally:
            extraction._llm_extract = original_llm
            extraction._sbert_embedding = original_sbert
            extraction._sbert_prototype_centroids.cache_clear()

    def test_sbert_stage_fail_open_keeps_candidate_on_embedding_error(self) -> None:
        original_llm = extraction._llm_extract
        original_sbert = extraction._sbert_embedding
        try:
            extraction._llm_extract = lambda _content: []
            extraction._sbert_prototype_centroids.cache_clear()

            def broken_sbert(_text: str, _model: str):
                raise RuntimeError("sbert unavailable")

            extraction._sbert_embedding = broken_sbert
            leads = extraction.extract_phone_leads(
                "Uma G <uma@brightpathstaffing.com>",
                "Java role",
                "Call me at +1 (214) 555-1212. Regards, recruiter.",
            )
            self.assertEqual(len(leads), 1)
            self.assertIn("sbert_error", leads[0].relevance_reason)
        finally:
            extraction._llm_extract = original_llm
            extraction._sbert_embedding = original_sbert
            extraction._sbert_prototype_centroids.cache_clear()

    def test_deterministic_noise_prefilter_runs_before_sbert(self) -> None:
        original_llm = extraction._llm_extract
        try:
            extraction._llm_extract = lambda _content: []
            extraction._sbert_prototype_centroids.cache_clear()
            with patch("app.premium_numbers.extraction._sbert_embedding") as mocked:
                body = (
                    "To unsubscribe from this group send an email to hstjava+unsubscribe@googlegroups.com. "
                    "https://groups.google.com/d/msgid/hstjava/01b001dceedd%2482913610%2487b3a230%24%40horizonsoftech.net"
                )
                leads = extraction.extract_phone_leads(
                    "Horizon Team <jobs@horizonsoftech.net>",
                    "Role",
                    body,
                    employer_domains={"horizonsoftech.net"},
                )
                self.assertEqual(leads, [])
                mocked.assert_not_called()
        finally:
            extraction._llm_extract = original_llm
            extraction._sbert_prototype_centroids.cache_clear()


if __name__ == "__main__":
    unittest.main()
