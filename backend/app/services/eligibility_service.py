from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from app.parsing.jd_requirements import ParsedJDRequirements


_AUTH_ALIASES = {
    "usc": "USC",
    "us citizen": "USC",
    "u s citizen": "USC",
    "citizen": "USC",
    "gc": "GC",
    "green card": "GC",
    "permanent resident": "GC",
    "h1b": "H1B",
    "h 1b": "H1B",
    "ead": "EAD",
    "tn": "TN",
}


def normalize_authorization(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return _AUTH_ALIASES.get(normalized, normalized.upper())


def _normalize_location(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


@dataclass(frozen=True)
class CandidateProfile:
    work_authorizations: tuple[str, ...] = ()
    total_experience_years: float | None = None
    us_experience_years: float | None = None
    current_location: str = ""


@dataclass(frozen=True)
class EligibilityResult:
    status: str
    reason_codes: tuple[str, ...] = ()
    evidence: dict[str, object] = field(default_factory=dict)


class EligibilityService:
    def evaluate(self, requirements: ParsedJDRequirements, profile: CandidateProfile) -> EligibilityResult:
        blocked: list[str] = []
        review: list[str] = []
        allowed = {normalize_authorization(value) for value in requirements.allowed_work_authorizations if value}
        actual = {normalize_authorization(value) for value in profile.work_authorizations if value}

        if allowed:
            if not actual:
                review.append("work_authorization_missing")
            elif allowed.isdisjoint(actual):
                blocked.append("work_authorization_mismatch")

        if requirements.experience_years_min is not None:
            if profile.total_experience_years is None:
                review.append("total_experience_missing")
            elif profile.total_experience_years < requirements.experience_years_min:
                blocked.append("total_experience_shortfall")

        if requirements.us_experience_years_min is not None:
            if profile.us_experience_years is None:
                review.append("us_experience_missing")
            elif profile.us_experience_years < requirements.us_experience_years_min:
                blocked.append("us_experience_shortfall")

        if requirements.local_required and requirements.locations:
            current = _normalize_location(profile.current_location)
            required_locations = {_normalize_location(value) for value in requirements.locations if value}
            if not current:
                review.append("current_location_missing")
            elif required_locations and all(location not in current and current not in location for location in required_locations):
                blocked.append("local_location_mismatch")

        evidence = {
            "requirements": {
                "allowed_work_authorizations": sorted(allowed),
                "total_experience_years_min": requirements.experience_years_min,
                "us_experience_years_min": requirements.us_experience_years_min,
                "local_required": requirements.local_required,
                "locations": list(requirements.locations),
            },
            "candidate_profile": {
                "work_authorizations": sorted(actual),
                "total_experience_years": profile.total_experience_years,
                "us_experience_years": profile.us_experience_years,
                "current_location": profile.current_location,
            },
        }
        if blocked:
            return EligibilityResult(status="blocked", reason_codes=tuple(dict.fromkeys(blocked)), evidence=evidence)
        if review:
            return EligibilityResult(status="needs_review", reason_codes=tuple(dict.fromkeys(review)), evidence=evidence)
        return EligibilityResult(status="pass", evidence=evidence)


def apply_inherited_constraints(
    requirements: ParsedJDRequirements,
    constraints: Sequence[Mapping[str, object]],
) -> ParsedJDRequirements:
    authorizations = list(requirements.allowed_work_authorizations)
    total_min = requirements.experience_years_min
    us_min = requirements.us_experience_years_min
    locations = list(requirements.locations)
    work_mode = requirements.work_mode
    for constraint in constraints:
        kind = str(constraint.get("type") or "").strip()
        value = str(constraint.get("value") or "").strip()
        if not value:
            continue
        if kind == "work_authorization":
            lowered = value.casefold()
            if "usc" in lowered or "citizen" in lowered:
                authorizations.append("USC")
            if "gc" in lowered or "green card" in lowered:
                authorizations.append("GC")
            if "h1b" in lowered:
                authorizations.append("H1B")
            if "ead" in lowered:
                authorizations.append("EAD")
            if re.search(r"\btn\b", lowered):
                authorizations.append("TN")
        elif kind in {"total_experience", "us_experience"}:
            match = re.search(r"\b(\d{1,2})\s*\+?", value)
            if match:
                years = int(match.group(1))
                if kind == "total_experience":
                    total_min = max(total_min or 0, years)
                else:
                    us_min = max(us_min or 0, years)
        elif kind == "location":
            locations.append(value)
        elif kind == "work_mode":
            work_mode = value
    return replace(
        requirements,
        allowed_work_authorizations=tuple(dict.fromkeys(authorizations)),
        experience_years_min=total_min,
        us_experience_years_min=us_min,
        locations=tuple(dict.fromkeys(locations)),
        work_mode=work_mode,
    )
