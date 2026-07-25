from __future__ import annotations

import json

from app.models import RecruiterEmail


STRUCTURAL_BLOCKING_STATUSES = frozenset(
    {
        "manifest_review",
        "extraction_review",
        "superseded_multi_role",
        "source_parent",
    }
)
ELIGIBILITY_BLOCKING_STATUSES = frozenset({"blocked_ineligible", "eligibility_review"})
MANDATORY_BLOCKING_STATUSES = frozenset({"mandatory_resume_fail", "mandatory_resume_review"})
STRICT_SCREENING_BLOCKING_STATUSES = ELIGIBILITY_BLOCKING_STATUSES | MANDATORY_BLOCKING_STATUSES


def mandatory_gate_status(email: RecruiterEmail) -> str:
    for raw in (email.resume_picker_breakdown_json, email.ats_breakdown_json):
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            value = str(payload.get("mandatory_gate_status") or "").strip().casefold()
            if value:
                return value
    return ""


def _has_mandatory_requirements(email: RecruiterEmail) -> bool:
    try:
        details = json.loads(email.parser_details_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return False
    structured = details.get("structured_requirements") if isinstance(details, dict) else None
    groups = structured.get("required_groups") if isinstance(structured, dict) else None
    return isinstance(groups, list) and any(isinstance(group, dict) and group.get("skills") for group in groups)


def _strict_mandatory_gate(email: RecruiterEmail) -> tuple[str, str | None]:
    status = mandatory_gate_status(email)
    if status == "fail":
        return status, "mandatory_resume_fail"
    if status in {"needs_review", "review"} and _has_mandatory_requirements(email):
        return status, "mandatory_resume_review"
    return status, None


def resolve_sendability_status(email: RecruiterEmail) -> str:
    stored = email.sendability_status
    if stored in STRUCTURAL_BLOCKING_STATUSES:
        return stored
    if stored in ELIGIBILITY_BLOCKING_STATUSES:
        return stored
    strict = email.screening_mode == "strict"
    if strict and stored in MANDATORY_BLOCKING_STATUSES:
        return stored
    if not strict and stored in MANDATORY_BLOCKING_STATUSES:
        return stored if email.screening_mode is None else "sendable"
    if stored == "sendable":
        return email.sendability_status
    if email.is_source_parent:
        return "source_parent"
    if strict:
        gate_status, blocking_status = _strict_mandatory_gate(email)
        if blocking_status is not None:
            return blocking_status
        if gate_status in {"pass", "not_applicable"}:
            return "sendable"
    if email.state == "needs_review" and (email.draft_reply or "").strip():
        return "sendable"
    return "not_ready"


def apply_resume_sendability(email: RecruiterEmail) -> str:
    if email.sendability_status in STRUCTURAL_BLOCKING_STATUSES:
        return email.sendability_status
    strict = email.screening_mode == "strict"
    if not strict:
        if email.sendability_status in STRICT_SCREENING_BLOCKING_STATUSES:
            email.sendability_status = None
        email.sendability_status = (
            "sendable"
            if email.state == "needs_review" and (email.draft_reply or "").strip()
            else None
        )
        return email.sendability_status or "not_ready"
    if email.sendability_status in ELIGIBILITY_BLOCKING_STATUSES:
        return email.sendability_status
    gate_status, blocking_status = _strict_mandatory_gate(email)
    email.sendability_status = blocking_status
    if email.sendability_status is None and gate_status in {
        "needs_review",
        "review",
        "pass",
        "not_applicable",
    }:
        email.sendability_status = "sendable"
    elif email.sendability_status is None:
        email.sendability_status = (
            "sendable"
            if email.state == "needs_review" and (email.draft_reply or "").strip()
            else None
        )
    if email.sendability_status in MANDATORY_BLOCKING_STATUSES:
        email.draft_reply = ""
        email.draft_source = None
        email.draft_model = None
        email.resume_asset_id = None
        email.resume_file_name = None
    return email.sendability_status or "not_ready"
