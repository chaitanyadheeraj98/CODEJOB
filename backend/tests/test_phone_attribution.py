import unittest

from app.phone_attribution import analyze_phone_attribution


class PhoneAttributionTests(unittest.TestCase):
    def test_recruiter_signature_phone_scores_high(self) -> None:
        body = (
            "Hi, this is Uma from BrightPath Staffing.\n"
            "Please call me at +1 (214) 555-1212 to discuss the role.\n"
            "Best regards,\nUma G\nSenior Recruiter"
        )
        result = analyze_phone_attribution(
            sender="Uma G <uma@brightpathstaffing.com>",
            subject="Java role",
            body=body,
            snippet="call me",
            to_email="candidate@example.com",
            cc_email="manager@clientcorp.com",
        )
        self.assertEqual(result.recruiter_phone, "+1 (214) 555-1212")
        self.assertGreaterEqual(result.recruiter_confidence, 0.75)

    def test_client_phone_in_quoted_history_is_not_premium(self) -> None:
        body = (
            "Hi, please share resume.\n"
            "-----Original Message-----\n"
            "From: Hiring Manager <hm@clientcorp.com>\n"
            "Call me at +1 972 555 8888 for client interview scheduling."
        )
        result = analyze_phone_attribution(
            sender="Recruiter <r@agency.com>",
            subject="Role",
            body=body,
            snippet="",
            to_email="candidate@example.com",
            cc_email="hm@clientcorp.com",
        )
        self.assertLess(result.recruiter_confidence, 0.75)
        self.assertIn(result.recruiter_reason, {"ambiguous", "not_found"})


if __name__ == "__main__":
    unittest.main()
