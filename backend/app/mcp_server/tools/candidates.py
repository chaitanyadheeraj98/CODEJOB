from __future__ import annotations

import difflib
import json
import re
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, or_

from app.config import settings
from app.db import SessionLocal
from app.models import RecruiterEmail
from app.services.sendability_service import resolve_sendability_status

VALID_CANDIDATE_STATUSES = (
    "needs_review",
    "approved_sent",
    "auto_rejected",
    "dismissed",
    "failed",
    "processed_skipped",
    "rejected",
)


def _resolve_status(status: str) -> tuple[str | None, str | None]:
    """Normalize a free-text status and match it to a valid queue status.

    Returns (resolved_status, error). Falls back to fuzzy matching so
    plural/typo/casing variants (e.g. "need_review", "Needs Review",
    "nead review") still resolve, instead of silently returning zero rows.
    """
    normalized = status.strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None, None
    if normalized in VALID_CANDIDATE_STATUSES:
        return normalized, None
    match = difflib.get_close_matches(normalized, VALID_CANDIDATE_STATUSES, n=1, cutoff=0.6)
    if match:
        return match[0], None
    return None, (
        f"Unknown status '{status}'. Valid values: {', '.join(VALID_CANDIDATE_STATUSES)}."
    )


def _parse_date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _summary(row: RecruiterEmail) -> dict[str, object]:
    return {
        "id": row.id,
        "record_id": row.record_id,
        "state": row.state,
        "score": row.score,
        "ats_score": row.ats_score,
        "decision": row.decision,
        "created_at": row.created_at.isoformat(),
        "untrusted_candidate_data": (
            "<untrusted_candidate_data>\n"
            f"Role: {row.role}\nLocation: {row.location}\n"
            f"Sender: {row.sender}\nSubject: {row.subject}\n"
            "</untrusted_candidate_data>"
        ),
    }


def _sent_attachment_names(row: RecruiterEmail) -> list[str]:
    try:
        values = json.loads(row.sent_attachment_file_names_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(value) for value in values] if isinstance(values, list) else []


def search_candidates(query: str = "", status: str = "", limit: int = 10) -> dict[str, object]:
    """Search the owner's candidates by text and optional queue status (fuzzy-matched).

    Each result has both "score" (AI match score x100, not ATS) and "ats_score"
    (the real ATS score) - use ats_score when the user asks about ATS scores,
    ranking, or "best" candidates by ATS.
    """
    db = SessionLocal()
    try:
        resolved_status, status_error = _resolve_status(status)
        if status_error:
            return {"error": status_error}
        rows = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id)
        if resolved_status:
            rows = rows.filter(RecruiterEmail.state == resolved_status)
        if query.strip():
            stripped = query.strip()
            term = f"%{stripped}%"
            conditions = [
                RecruiterEmail.role.ilike(term),
                RecruiterEmail.subject.ilike(term),
                RecruiterEmail.sender.ilike(term),
                RecruiterEmail.skills_text.ilike(term),
            ]
            ids_in_query = [int(n) for n in re.findall(r"\d+", stripped)]
            if ids_in_query:
                conditions.append(RecruiterEmail.id.in_(ids_in_query))
            rows = rows.filter(or_(*conditions))
        total = rows.count()
        visible = rows.order_by(RecruiterEmail.created_at.desc()).limit(max(1, min(limit, 25))).all()
        return {"candidates": [_summary(row) for row in visible], "count": total}
    finally:
        db.close()


def propose_bulk_approve_candidates(
    query: str = "", status: str = "needs_review", limit: int = 25
) -> dict[str, object]:
    """Prepare, but never execute, a proposal to approve matching review candidates."""
    result = search_candidates(query=query, status=status, limit=limit)
    if "error" in result:
        return result
    candidates = [
        row for row in result.get("candidates", [])
        if isinstance(row, dict) and row.get("state") == "needs_review"
    ]
    return {
        "action": "approve_candidates",
        "candidate_ids": [row["id"] for row in candidates],
        "candidates": candidates,
        "count": len(candidates),
    }


def get_candidate(email_id: int) -> dict[str, object]:
    """Get one owner-scoped candidate with untrusted source text clearly delimited."""
    db = SessionLocal()
    try:
        row = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if row is None:
            return {"error": "Candidate not found"}
        payload = _summary(row)
        payload.update(
            {
                "sendability_status": row.sendability_status,
                "resume_asset_id": row.resume_asset_id,
                "resume_file_name": row.resume_file_name,
                "sent_attachment_file_names": _sent_attachment_names(row),
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                "ats_score_source": row.ats_score_source,
                "ai_score": row.ai_score,
                "ai_score_source": row.ai_score_source,
                "resume_picker_score": row.resume_picker_score,
                "resume_picker_reason": row.resume_picker_reason,
                "decision_reason": row.decision_reason,
                "auto_reject_reason": row.auto_reject_reason,
                "hard_filter_result": row.hard_filter_result,
                "untrusted_source_data": (
                    "<untrusted_candidate_data>\n"
                    f"Role: {row.role}\nLocation: {row.location}\n"
                    f"Sender: {row.sender}\nSubject: {row.subject}\n"
                    f"Salary: {row.salary_text}\nSkills: {row.skills_text}\n"
                    f"AI summary: {row.ai_summary}\nATS summary: {row.ats_summary}\n"
                    f"Body: {row.body[:8000]}\n"
                    "</untrusted_candidate_data>"
                ),
            }
        )
        return payload
    finally:
        db.close()


def count_received_emails(date_from: str = "", date_to: str = "", sender: str = "", limit: int = 10) -> dict[str, object]:
    """Count/list owner-scoped recruiter emails received within an inclusive date range.

    date_from/date_to use YYYY-MM-DD (UTC calendar days, not the user's local
    timezone). Omit date_to to query a single day. Matches the actual Gmail
    receipt time when known, falling back to the row's created_at for
    manually-added emails. Optionally filter by sender substring.
    """
    db = SessionLocal()
    try:
        start = _parse_date(date_from)
        if date_from and start is None:
            return {"error": f"Invalid date_from '{date_from}'. Use YYYY-MM-DD."}
        end = _parse_date(date_to) if date_to else start
        if date_to and end is None:
            return {"error": f"Invalid date_to '{date_to}'. Use YYYY-MM-DD."}

        received_at = func.coalesce(RecruiterEmail.gmail_received_at, RecruiterEmail.created_at)
        rows = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id)
        if start is not None:
            rows = rows.filter(received_at >= datetime.combine(start, time.min))
        if end is not None:
            rows = rows.filter(received_at < datetime.combine(end + timedelta(days=1), time.min))
        if sender.strip():
            rows = rows.filter(RecruiterEmail.sender.ilike(f"%{sender.strip()}%"))

        total = rows.count()
        visible = rows.order_by(received_at.desc()).limit(max(1, min(limit, 25))).all()
        return {"count": total, "candidates": [_summary(row) for row in visible]}
    finally:
        db.close()


_SENDABILITY_REASONS = {
    "sendable": "A draft is ready to review and send.",
    "not_ready": "No usable draft has been generated for this email yet.",
    "manifest_review": "Role-manifest extraction is still pending review.",
    "extraction_review": "Requirement extraction result is pending review.",
    "score_review": "Score/decision is still pending review.",
    "superseded_multi_role": "Superseded by a split multi-role child email; check the child posting instead.",
    "source_parent": "This is the original multi-role parent email; drafts are generated on its child postings, not here.",
    "blocked_ineligible": "Candidate was marked ineligible for this role.",
    "eligibility_review": "Eligibility check has not been completed yet.",
    "mandatory_resume_fail": "The resume failed a mandatory-skill check under strict screening.",
    "mandatory_resume_review": "A mandatory-skill match needs manual review under strict screening.",
}


def get_draft_status(email_id: int) -> dict[str, object]:
    """Diagnose why a candidate email does or doesn't have a ready-to-send draft."""
    db = SessionLocal()
    try:
        row = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
            .first()
        )
        if row is None:
            return {"error": "Candidate not found"}
        has_draft = bool((row.draft_reply or "").strip())
        status = resolve_sendability_status(row)
        return {
            "id": row.id,
            "has_draft": has_draft,
            "sendability_status": status,
            "sendability_reason": _SENDABILITY_REASONS.get(status, ""),
            "draft_source": row.draft_source,
            "draft_model": row.draft_model,
            "draft_ai_error": row.draft_ai_error,
            "draft_resume_context_status": row.draft_resume_context_status,
            "approval_status": row.approval_status,
            "sent_status": row.sent_status,
            "resume_asset_id": row.resume_asset_id,
            "resume_file_name": row.resume_file_name,
            "sent_attachment_file_names": _sent_attachment_names(row),
            "sent_at": row.sent_at.isoformat() if row.sent_at else None,
            "draft_quality": row.draft_quality if has_draft else None,
        }
    finally:
        db.close()
