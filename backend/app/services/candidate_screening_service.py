from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.models import RecruiterEmail, UserSettings
from app.parsing.jd_requirements import ParsedJDRequirements
from app.services.eligibility_service import (
    CandidateProfile,
    apply_inherited_constraints,
    evaluate_eligibility,
)


@dataclass(frozen=True)
class CandidateScreeningDecision:
    mode: str
    eligibility_status: str
    proceed_to_scoring: bool
    enforce_mandatory_resume_gate: bool
    reason_codes: tuple[str, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)


class CandidateScreeningService:
    def evaluate(
        self,
        requirements: ParsedJDRequirements,
        profile: CandidateProfile,
        *,
        strict_enabled: bool,
    ) -> CandidateScreeningDecision:
        if not strict_enabled:
            return CandidateScreeningDecision(
                mode="compatibility",
                eligibility_status="not_enforced",
                proceed_to_scoring=True,
                enforce_mandatory_resume_gate=False,
            )

        eligibility = evaluate_eligibility(requirements, profile)
        return CandidateScreeningDecision(
            mode="strict",
            eligibility_status=eligibility.status,
            proceed_to_scoring=eligibility.status == "pass",
            enforce_mandatory_resume_gate=True,
            reason_codes=eligibility.reason_codes,
            evidence=eligibility.evidence,
        )

    def evaluate_parser_details(
        self,
        parser_details: Mapping[str, Any],
        settings: UserSettings,
        *,
        inherited_constraints: list[Mapping[str, object]] | None = None,
    ) -> CandidateScreeningDecision:
        from app.parsing.jd_requirements import requirements_from_payload, requirements_to_payload

        payload = parser_details.get("structured_requirements")
        requirements = requirements_from_payload(payload if isinstance(payload, Mapping) else None)
        if inherited_constraints:
            requirements = apply_inherited_constraints(requirements, inherited_constraints)
            if isinstance(parser_details, dict):
                parser_details["structured_requirements"] = requirements_to_payload(requirements)
        return self.evaluate(
            requirements,
            CandidateProfile(
                work_authorizations=tuple(_json_string_list(settings.candidate_work_authorizations_json)),
                total_experience_years=settings.candidate_total_experience_years,
                us_experience_years=settings.candidate_us_experience_years,
                current_location=settings.candidate_current_location or "",
            ),
            strict_enabled=bool(settings.feature_strict_candidate_screening_enabled),
        )


def apply_screening_decision(email: RecruiterEmail, decision: CandidateScreeningDecision) -> None:
    email.screening_mode = decision.mode
    email.eligibility_status = decision.eligibility_status
    email.eligibility_details_json = json.dumps(
        {
            "status": decision.eligibility_status,
            "reason_codes": list(decision.reason_codes),
            "evidence": decision.evidence,
        },
        separators=(",", ":"),
    )
    if decision.proceed_to_scoring:
        if email.sendability_status in {
            "blocked_ineligible",
            "eligibility_review",
            "mandatory_resume_fail",
            "mandatory_resume_review",
        }:
            email.sendability_status = None
        return
    email.sendability_status = (
        "blocked_ineligible" if decision.eligibility_status == "blocked" else "eligibility_review"
    )
    email.ats_score = None
    email.ats_score_source = None
    email.ats_summary = None
    email.ats_breakdown_json = None
    email.resume_picker_score = None
    email.resume_picker_reason = None
    email.resume_picker_candidates_json = None
    email.resume_picker_breakdown_json = None
    email.resume_asset_id = None
    email.resume_file_name = None
    email.draft_reply = ""
    email.draft_source = None
    email.draft_model = None


def _json_string_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]
