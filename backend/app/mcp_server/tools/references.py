from __future__ import annotations

import difflib

from app.config import settings
from app.db import SessionLocal
from app.models import PremiumNumberContact, RecruiterEmail, RecruiterOpportunity

MAX_OPTIONS = 10
FUZZY_CUTOFF = 0.6

# kind -> the fields searched, named in the payload so a zero-result answer
# says why rather than just "nothing found".
SEARCHED_FIELDS = {
    "contact": ["recruiter_name", "owner_name", "company", "recruiter_email"],
    "opportunity": ["job_title", "end_client", "email_sender"],
    "candidate": ["role", "sender", "subject"],
}


def _contacts(db) -> list[dict[str, object]]:
    rows = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.deleted_at.is_(None),
    ).all()
    return [
        {
            "id": row.id,
            "label": row.recruiter_name or row.owner_name or row.display_phone_number or f"Contact {row.id}",
            # Without a distinguishing detail the user is picking between two
            # identical names, which is the failure this whole tool exists to
            # prevent.
            "detail": " · ".join(part for part in (row.company, row.display_phone_number) if part),
            "haystack": " ".join(str(value or "") for value in (
                row.recruiter_name, row.owner_name, row.company, row.recruiter_email
            )),
        }
        for row in rows
    ]


def _opportunities(db) -> list[dict[str, object]]:
    rows = db.query(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == settings.owner_id,
    ).all()
    return [
        {
            "id": row.id,
            "label": row.job_title or f"Opportunity {row.id}",
            "detail": " · ".join(part for part in (
                row.end_client,
                row.received_at.date().isoformat() if row.received_at else "",
            ) if part),
            "haystack": " ".join(str(value or "") for value in (row.job_title, row.end_client, row.email_sender)),
        }
        for row in rows
    ]


def _candidates(db) -> list[dict[str, object]]:
    rows = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id).all()
    return [
        {
            "id": row.id,
            "label": row.role or row.subject or f"Candidate {row.id}",
            "detail": " · ".join(part for part in (
                row.sender,
                row.created_at.date().isoformat() if row.created_at else "",
            ) if part),
            "haystack": " ".join(str(value or "") for value in (row.role, row.sender, row.subject)),
        }
        for row in rows
    ]


LOADERS = {"contact": _contacts, "opportunity": _opportunities, "candidate": _candidates}


def resolve_record_reference(kind: str, query: str, limit: int = 5) -> dict[str, object]:
    """Find records matching a name the user typed. Returns one match, several, or none.

    kind is contact, opportunity, or candidate. Call this before acting on a
    record the user named in words rather than by id - "update Sarah's status",
    "the Java role from BigCo".

    Three outcomes, and they mean different things. One confident match
    resolves and you may proceed. Several plausible matches return a list the
    user picks from - do not choose for them, and do not restate the options as
    prose; they are already shown a chooser. No match names the fields that
    were searched.
    """
    if kind not in LOADERS:
        return {"error": f"Unknown kind '{kind}'.", "kinds": sorted(LOADERS)}

    text = (query or "").strip()
    if not text:
        return {"status": "missing_fields", "missing": ["query"]}

    capped = max(1, min(int(limit), MAX_OPTIONS))
    needle = text.lower()

    db = SessionLocal()
    try:
        rows = LOADERS[kind](db)
    finally:
        db.close()

    # Exact-first, fuzzy-second, matching the pattern _resolve_status and
    # get_app_help already use.
    exact = [row for row in rows if needle == str(row["label"]).lower()]
    substring = [row for row in rows if row not in exact and needle in str(row["haystack"]).lower()]
    matches = exact + substring

    if not matches:
        close = difflib.get_close_matches(needle, [str(row["haystack"]).lower() for row in rows], n=capped, cutoff=FUZZY_CUTOFF)
        seen: set[int] = set()
        for candidate in close:
            for row in rows:
                if str(row["haystack"]).lower() == candidate and row["id"] not in seen:
                    seen.add(int(row["id"]))
                    matches.append(row)

    options = [
        {"id": row["id"], "label": row["label"], "detail": row["detail"]}
        for row in matches[:capped]
    ]

    if not options:
        # A cross-owner record is indistinguishable from a nonexistent one:
        # every loader is owner-scoped, so it simply is not in `rows`.
        return {"error": f"No {kind} found", "query": text, "searched": SEARCHED_FIELDS[kind]}

    if len(options) == 1:
        return {"action": "resolved", "kind": kind, "id": options[0]["id"], "label": options[0]["label"]}

    return {
        "action": "render_disambiguation",
        "kind": kind,
        "query": text,
        "options": options,
        "truncated": len(matches) > capped,
    }
