import unittest

from app.parsing.jd_requirements import ParsedJDRequirements
from app.services.eligibility_service import CandidateProfile
from app.services.candidate_screening_service import CandidateScreeningService


class CandidateScreeningServiceTests(unittest.TestCase):
    def test_compatibility_mode_keeps_profile_mismatch_advisory(self) -> None:
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

        result = CandidateScreeningService().evaluate(
            requirements,
            profile,
            strict_enabled=False,
        )

        self.assertEqual(result.mode, "compatibility")
        self.assertEqual(result.eligibility_status, "not_enforced")
        self.assertTrue(result.proceed_to_scoring)
        self.assertFalse(result.enforce_mandatory_resume_gate)
        self.assertEqual(result.reason_codes, ())

    def test_strict_mode_blocks_profile_mismatch_before_scoring(self) -> None:
        requirements = ParsedJDRequirements(
            allowed_work_authorizations=("USC", "GC"),
            experience_years_min=14,
            us_experience_years_min=10,
        )
        profile = CandidateProfile(
            work_authorizations=("H1B",),
            total_experience_years=7,
            us_experience_years=7,
        )

        result = CandidateScreeningService().evaluate(
            requirements,
            profile,
            strict_enabled=True,
        )

        self.assertEqual(result.mode, "strict")
        self.assertEqual(result.eligibility_status, "blocked")
        self.assertFalse(result.proceed_to_scoring)
        self.assertTrue(result.enforce_mandatory_resume_gate)
        self.assertEqual(
            set(result.reason_codes),
            {"work_authorization_mismatch", "total_experience_shortfall", "us_experience_shortfall"},
        )


if __name__ == "__main__":
    unittest.main()
