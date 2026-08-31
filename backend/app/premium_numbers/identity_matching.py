from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models import PremiumNumberContact
from app.premium_numbers.extraction import ExtractedContactGroup


@dataclass(frozen=True)
class MatchResult:
    outcome: Literal["confirmed", "conflicting", "insufficient"]
    reason: str


def _normalized(value: str | None) -> str:
    text = " ".join((value or "").split()).casefold()
    return "" if text == "unknown" else text


def classify_identity_match(
    existing: PremiumNumberContact,
    candidate: ExtractedContactGroup,
    role: str,
) -> MatchResult:
    current = {
        "name": _normalized(existing.recruiter_name if role == "recruiter" else existing.owner_name),
        "email": _normalized(existing.recruiter_email if role == "recruiter" else existing.employer_email),
        "company": _normalized(existing.company),
        "extension": _normalized(existing.phone_extension),
    }
    incoming = {
        "name": _normalized(candidate.owner_name),
        "email": _normalized(candidate.contact_email),
        "company": _normalized(candidate.company),
        "extension": _normalized(candidate.phone_extension),
    }

    if current["extension"] and incoming["extension"] and current["extension"] != incoming["extension"]:
        return MatchResult("conflicting", "extension_mismatch")
    if current["email"] and current["email"] == incoming["email"]:
        return MatchResult("confirmed", "email_match")
    if (
        current["name"]
        and current["name"] == incoming["name"]
        and (not current["company"] or not incoming["company"] or current["company"] == incoming["company"])
    ):
        return MatchResult("confirmed", "name_match_company_compatible")

    agreements = [field for field in current if current[field] and current[field] == incoming[field]]
    if len(agreements) >= 2:
        return MatchResult("confirmed", f"identity_fields_match:{','.join(agreements)}")

    conflicts = [
        field
        for field in current
        if current[field] and incoming[field] and current[field] != incoming[field]
    ]
    if conflicts:
        return MatchResult("conflicting", f"identity_fields_conflict:{','.join(conflicts)}")
    return MatchResult("insufficient", "phone_match_without_enough_identity_evidence")
