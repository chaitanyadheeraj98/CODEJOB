"""Explain, for one logged submission, why that resume was the one that went out.

The submission's own skill-gap snapshot cannot answer this. It compares the resume
against the application's linked job description - and no application in practice
has one: `recruiter_opportunity_id` is null across the board and `manual_jd_text` is
empty, so the comparison runs against an empty string and every list comes back
empty. That is why the panel has always read "Missing required: None".

The answer does exist, on the email the submission came from. The scoring pipeline
writes a full resume-picker breakdown there - which resume won, what it matched,
what it was missing, and how the runners-up scored. Applications reach that email
through `dedupe_key`, which the backfill wrote as "recruiter_email:<id>".

Read-only. No new tables, no migration.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Application, RecruiterEmail, ResumeAsset
from app.schemas import resume_variant_code
from app.services.role_gap_service import clean_missing_skills

DEDUPE_EMAIL_PREFIX = 'recruiter_email:'


def linked_email_id(application: Application) -> int | None:
    """Recover the email id a submission was backfilled from.

    The link lives in `dedupe_key` rather than a foreign key because these rows were
    reconstructed from already-sent mail; the key was what made that import
    idempotent, and it is the only handle back to the source.
    """
    key = (application.dedupe_key or '').strip()
    if not key.startswith(DEDUPE_EMAIL_PREFIX):
        return None
    raw = key[len(DEDUPE_EMAIL_PREFIX):].strip()
    return int(raw) if raw.isdigit() else None


def _json_obj(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _str_list(value: object, *, limit: int = 40) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or '').strip()][:limit]


def _number(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _unavailable(reason: str, email_id: int | None = None) -> dict[str, Any]:
    return {'available': False, 'reason_unavailable': reason, 'email_id': email_id}


def why_this_resume(db: Session, application: Application) -> dict[str, Any]:
    email_id = linked_email_id(application)
    if email_id is None:
        return _unavailable(
            'This submission was logged by hand, so there is no scored email behind it.'
        )

    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == application.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if email is None:
        return _unavailable('The email this submission came from is no longer stored.', email_id)

    breakdown = _json_obj(email.resume_picker_breakdown_json)
    if not breakdown:
        return _unavailable(
            'This email was sent before resume scoring was recorded, so there is no breakdown to show.',
            email_id,
        )

    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == application.owner_id, ResumeAsset.id == email.resume_asset_id)
        .first()
        if email.resume_asset_id is not None
        else None
    )

    return {
        'available': True,
        'reason_unavailable': None,
        'email_id': email.id,
        'variant_code': resume_variant_code(email.resume_asset_id),
        'variant_label': (resume.variant_label if resume else '') or '',
        'resume_file_name': email.resume_file_name or '',
        'jd_role': (email.role or '').strip(),
        'selection_status': str(breakdown.get('selection_status') or ''),
        'selection_warning': str(breakdown.get('selection_warning') or '') or None,
        'mandatory_gate_status': str(breakdown.get('mandatory_gate_status') or ''),
        'mandatory_coverage': _number(breakdown.get('mandatory_coverage')),
        'matched_required': _str_list(breakdown.get('mandatory_matched_skills')),
        # Same fragment stripping the build list gets: the extractor emits
        # "React Hooks", "Hooks" and "Context" alongside "Context API", and listing
        # all four makes the gap look bigger and vaguer than it is.
        'missing_required': clean_missing_skills(_str_list(breakdown.get('mandatory_missing_skills'))),
        'matched_priority': _str_list(breakdown.get('matched_priority_skills')),
        'missing_priority': clean_missing_skills(_str_list(breakdown.get('missing_priority_skills'))),
        'role_family_fit': _number(breakdown.get('role_family_fit_score')),
        'jd_role_family': str(breakdown.get('jd_role_family') or ''),
        'final_resume_score': _number(breakdown.get('final_resume_score')),
        'ats_score': _number(email.ats_score),
        'ats_summary': (email.ats_summary or '').strip() or None,
        'picker_reason': (email.resume_picker_reason or '').strip() or None,
        'alternatives': _alternatives(db, email, application.owner_id),
    }


def _alternatives(db: Session, email: RecruiterEmail, owner_id: str) -> list[dict[str, Any]]:
    """The runners-up, so "why this one" is answerable against what else was available."""
    candidates = _json_obj(email.resume_picker_candidates_json)
    rankings = candidates.get('rankings')
    if not isinstance(rankings, list):
        return []

    selected = str(candidates.get('selected_resume_file_name') or email.resume_file_name or '')
    by_file_name = {
        row.file_name: row.id
        for row in db.query(ResumeAsset).filter(ResumeAsset.owner_id == owner_id).all()
    }

    out: list[dict[str, Any]] = []
    for entry in rankings[:6]:
        if not isinstance(entry, dict):
            continue
        file_name = str(entry.get('resume_file_name') or '')
        out.append(
            {
                'variant_code': resume_variant_code(by_file_name.get(file_name)),
                'resume_file_name': file_name,
                'final_resume_score': _number(entry.get('final_resume_score')),
                'ats_score': _number(entry.get('ats_score')),
                'selection_reason': str(entry.get('selection_reason') or '').strip() or None,
                'is_selected': bool(file_name) and file_name == selected,
            }
        )
    return out
