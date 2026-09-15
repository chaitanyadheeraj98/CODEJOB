"""has_job_description_content: the deterministic backstop for drafting.

A bare "here is a link, fill in these fields" email hits `skills_text=
"none_detected"` and an empty structured_requirements payload - this is the
exact shape the parser produces for that case (confirmed against the prod
incident row). A real requirement, even a thin one, produces at least one of
a matched skill, an experience floor, a location, or a work mode.
"""

import unittest

from app.parsing.jd_requirements import (
    ParsedJDRequirements,
    has_job_description_content,
    requirements_from_payload,
    structured_requirements_from_ai_payload,
)

EMPTY_PAYLOAD = {
    "schema_version": 1,
    "required_groups": [],
    "preferred_groups": [],
    "informational_groups": [],
    "experience_years_min": None,
    "local_required": False,
    "work_mode": None,
    "locations": [],
    "warnings": [],
    "preferred_domains": [],
}


class HasJobDescriptionContentTests(unittest.TestCase):
    def test_none_detected_skills_and_empty_structured_requirements_is_insufficient(self) -> None:
        self.assertFalse(
            has_job_description_content(
                skills_text="none_detected",
                structured_requirements=requirements_from_payload(EMPTY_PAYLOAD),
            )
        )

    def test_blank_skills_text_is_insufficient(self) -> None:
        self.assertFalse(
            has_job_description_content(
                skills_text="",
                structured_requirements=requirements_from_payload(EMPTY_PAYLOAD),
            )
        )

    def test_real_skills_text_is_sufficient_even_with_empty_structured_requirements(self) -> None:
        self.assertTrue(
            has_job_description_content(
                skills_text="java, spring boot",
                structured_requirements=requirements_from_payload(EMPTY_PAYLOAD),
            )
        )

    def test_a_matched_required_skill_is_sufficient_even_with_none_detected_skills_text(self) -> None:
        payload = dict(EMPTY_PAYLOAD)
        payload["required_groups"] = [
            {
                "group_id": "spring-required",
                "level": "mandatory",
                "mode": "all",
                "skills": [
                    {
                        "skill_id": "spring_boot",
                        "canonical_name": "Spring Boot",
                        "matched_alias": "spring boot",
                        "evidence_text": "Spring Boot",
                        "versions": [],
                        "qualifiers": [],
                    }
                ],
                "evidence_text": "Spring Boot",
                "section_heading": "Required Skills",
                "section_bucket": "required",
            }
        ]
        self.assertTrue(
            has_job_description_content(
                skills_text="none_detected",
                structured_requirements=requirements_from_payload(payload),
            )
        )

    def test_an_experience_floor_alone_is_sufficient(self) -> None:
        payload = dict(EMPTY_PAYLOAD)
        payload["experience_years_min"] = 5
        self.assertTrue(
            has_job_description_content(
                skills_text="none_detected",
                structured_requirements=requirements_from_payload(payload),
            )
        )

    def test_a_location_alone_is_sufficient(self) -> None:
        payload = dict(EMPTY_PAYLOAD)
        payload["locations"] = ["Texas"]
        self.assertTrue(
            has_job_description_content(
                skills_text="none_detected",
                structured_requirements=requirements_from_payload(payload),
            )
        )

    def test_a_work_mode_alone_is_sufficient(self) -> None:
        payload = dict(EMPTY_PAYLOAD)
        payload["work_mode"] = "remote"
        self.assertTrue(
            has_job_description_content(
                skills_text="none_detected",
                structured_requirements=requirements_from_payload(payload),
            )
        )


class StructuredRequirementsFromAiPayloadTests(unittest.TestCase):
    def test_ai_visa_hints_override_rule_authorizations(self) -> None:
        result = structured_requirements_from_ai_payload(
            {"visa_hints": ["USC/GC only"]},
            fallback=ParsedJDRequirements(allowed_work_authorizations=("H1B",)),
        )

        self.assertEqual(result.allowed_work_authorizations, ("USC", "GC"))

    def test_empty_ai_visa_hints_keep_rule_authorizations(self) -> None:
        result = structured_requirements_from_ai_payload(
            {"visa_hints": []},
            fallback=ParsedJDRequirements(allowed_work_authorizations=("H1B",)),
        )

        self.assertEqual(result.allowed_work_authorizations, ("H1B",))

    def test_ai_locations_replace_rule_locations(self) -> None:
        result = structured_requirements_from_ai_payload(
            {
                "primary_location": "Dallas, TX",
                "mentioned_locations": ["Austin, TX", "Dallas, TX"],
            },
            fallback=ParsedJDRequirements(locations=("Rule Parser, ZZ",)),
        )

        self.assertEqual(result.locations, ("Dallas, TX", "Austin, TX"))

    def test_missing_ai_locations_keep_rule_locations(self) -> None:
        result = structured_requirements_from_ai_payload(
            {},
            fallback=ParsedJDRequirements(locations=("Rule Parser, ZZ",)),
        )

        self.assertEqual(result.locations, ("Rule Parser, ZZ",))


if __name__ == "__main__":
    unittest.main()
