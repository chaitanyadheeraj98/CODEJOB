from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy.orm import Session

from app.models import RecruiterEmail, UserSettings
from app.parsing import build_skills_json_payload
from app.parsing.jd_requirements import requirements_from_payload, requirements_to_payload
from app.schemas import RegenerateCandidateRequest
from app.services.candidate_screening_service import CandidateScreeningService, apply_screening_decision
from app.services.eligibility_service import apply_inherited_constraints
from app.services.role_provenance import apply_role_family

TERMINAL_STATES = {"approved_sent", "rejected", "auto_rejected"}


def extract_and_score_children(
    db: Session,
    child_ids: Sequence[int],
    *,
    user_settings: UserSettings,
    get_candidate: Callable[[Session, int], RecruiterEmail],
    parse_email_with_details: Callable[..., tuple[dict[str, Any], dict[str, Any]]],
    regenerate_candidate: Callable[[int, RegenerateCandidateRequest, Session], RecruiterEmail],
) -> None:
    for child_id in child_ids:
        child = get_candidate(db, child_id)
        if child.state in TERMINAL_STATES:
            continue
        try:
            parsed, parser_details = parse_email_with_details(
                child.subject,
                child.requirement_source_text or child.body,
                source=child.source,
                ai_extractor_enabled=user_settings.feature_ai_extractor_enabled,
            )
            inherited = json.loads(child.inherited_constraints_json or "[]")
            if not isinstance(inherited, list):
                inherited = []
            structured = requirements_from_payload(
                parser_details.get("structured_requirements")
                if isinstance(parser_details.get("structured_requirements"), Mapping)
                else None
            )
            structured = apply_inherited_constraints(
                structured,
                [item for item in inherited if isinstance(item, Mapping)],
            )
            parser_details["structured_requirements"] = requirements_to_payload(structured)
            screening = CandidateScreeningService().evaluate_parser_details(
                parser_details,
                user_settings,
            )
            child.role = str(parsed.get("role") or child.role)
            child.location = str(parsed.get("location") or "unknown")
            child.salary_text = str(parsed.get("salary_text") or "not_specified")
            child.skills_text = str(parsed.get("skills_text") or "none_detected")
            # The child was created from a title hint alone; now that its own block
            # has been parsed it gets classified against real skills.
            apply_role_family(child, role=child.role, skills_text=child.skills_text)
            child.parser_details_json = json.dumps(parser_details, separators=(",", ":"))
            child.skills_json = json.dumps(
                build_skills_json_payload(parser_details, fallback_skills_text=child.skills_text),
                separators=(",", ":"),
            )
            apply_screening_decision(child, screening)
            if not screening.proceed_to_scoring:
                db.commit()
                continue
            db.commit()
            regenerate_candidate(
                child.id,
                RegenerateCandidateRequest(preserve_manual_routing=True, preserve_review_visibility=True),
                db,
            )
        except Exception as exc:
            db.rollback()
            failed_child = get_candidate(db, child_id)
            failed_child.sendability_status = "extraction_review"
            failed_child.last_error = str(exc)
            db.commit()
