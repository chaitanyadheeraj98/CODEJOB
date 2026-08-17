import unittest

from app.parsing.jd_requirements import ParsedJDRequirements
from app.services.eligibility_service import CandidateProfile, evaluate_eligibility


class EligibilityServiceTests(unittest.TestCase):
    def test_h1b_and_seven_year_profile_is_blocked_before_scoring(self) -> None:
        requirements = ParsedJDRequirements(
            allowed_work_authorizations=("USC", "GC"),
            experience_years_min=14,
            us_experience_years_min=10,
        )
        profile = CandidateProfile(
            work_authorizations=("H1B",),
            total_experience_years=7,
            us_experience_years=7,
            current_location="Dallas, TX",
        )

        result = evaluate_eligibility(requirements, profile)

        self.assertEqual(result.status, "blocked")
        self.assertEqual(
            set(result.reason_codes),
            {"work_authorization_mismatch", "total_experience_shortfall", "us_experience_shortfall"},
        )


if __name__ == "__main__":
    unittest.main()
