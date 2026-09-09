from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import APPLICATION_STATUS_VALUES, Application, PremiumNumberContact, RecruiterOpportunity
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES

MAX_NOTE_CHARS = 2000
MAX_VALUE_CHARS = 500

# A field allowlist per record kind, narrower than the patch schemas accept.
#
# Deliberately excluded: evidence, extracted_skills, job_confidence,
# end_client_confirmed. Those are extraction outputs with provenance semantics -
# a model overwriting them launders a guess into the record as though a parser
# had found it.
#
# `notes` is excluded here too, because PATCH replaces it. Adding a note goes
# through propose_add_note, which shows the existing text.
UPDATABLE: dict[str, dict[str, object]] = {
    "opportunity": {
        "endpoint": "/recruiter-opportunities/{id}",
        "label": "Opportunity",
        "fields": {
            "status", "job_title", "location", "work_mode", "employment_type",
            "rate_amount", "rate_currency", "rate_unit", "end_client",
            "implementation_partner", "prime_vendor", "domain", "contract_duration",
        },
        "statuses": sorted(OPPORTUNITY_STATUS_VALUES),
    },
    "application": {
        "endpoint": "/applications/{id}",
        "label": "Application",
        "fields": {"status", "next_action_at"},
        "statuses": list(APPLICATION_STATUS_VALUES),
    },
    "contact": {
        "endpoint": "/recruiter-numbers/{id}",
        "label": "Contact",
        "fields": {"is_favorite", "do_not_work_again", "do_not_work_again_reason"},
        "statuses": None,
    },
}

_MODELS = {
    "opportunity": RecruiterOpportunity,
    "application": Application,
    "contact": PremiumNumberContact,
}

_NOTE_KINDS = ("opportunity", "application")


def _load(db, record_kind: str, record_id: int):
    model = _MODELS[record_kind]
    query = db.query(model).filter(model.owner_id == settings.owner_id, model.id == int(record_id))
    if record_kind in ("application", "contact"):
        query = query.filter(model.deleted_at.is_(None))
    return query.first()


def _display(value: object) -> object:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (bool, int, float)):
        return value
    return str(value)[:MAX_VALUE_CHARS]


def propose_record_update(record_kind: str, record_id: int, fields: dict[str, object]) -> dict[str, object]:
    """Prepare a change to one opportunity, application, or contact. Never performs it.

    record_kind is opportunity, application, or contact. `fields` carries only
    the values to change; anything outside the allowed list for that kind is
    refused and named.

    The card shows the current value beside the new one for every field, read
    from the database, so the user sees exactly what changes. Only their click
    executes it.

    To add a note, use propose_add_note - not this tool. Updating `notes` here
    would replace whatever is already stored.
    """
    spec = UPDATABLE.get(record_kind)
    if spec is None:
        return {"error": f"Unknown record kind '{record_kind}'.", "kinds": sorted(UPDATABLE)}

    allowed = spec["fields"]
    assert isinstance(allowed, set)
    requested = dict(fields or {})

    rejected = sorted(key for key in requested if key not in allowed)
    if rejected:
        return {
            "error": f"Fields not updatable on a {record_kind}: {', '.join(rejected)}.",
            "allowed_fields": sorted(allowed),
            **({"note": "Use propose_add_note to add a note."} if "notes" in rejected else {}),
        }

    accepted = {key: value for key, value in requested.items() if key in allowed}
    if not accepted:
        return {"hint": 'Ask the user for fields. Do not guess.', "status": "missing_fields", "missing": ["fields"], "allowed_fields": sorted(allowed)}

    statuses = spec.get("statuses")
    if "status" in accepted and statuses is not None and accepted["status"] not in statuses:
        # The valid list, never the nearest guess.
        return {"error": f"Invalid status '{accepted['status']}'.", "statuses": list(statuses)}

    db = SessionLocal()
    try:
        row = _load(db, record_kind, record_id)
        if row is None:
            return {"error": f"{spec['label']} not found", "record_id": int(record_id)}
        # Only the server can supply the "from" half of from -> to.
        changes = [
            {"field": key, "from": _display(getattr(row, key, None)), "to": _display(value)}
            for key, value in accepted.items()
        ]
        label = getattr(row, "job_title", None) or getattr(row, "recruiter_name", None) or f"{spec['label']} {record_id}"
    finally:
        db.close()

    return {
        "action": "propose_record_update",
        "record_kind": record_kind,
        "record_id": int(record_id),
        "record_label": str(label)[:120],
        "endpoint": str(spec["endpoint"]).format(id=int(record_id)),
        "fields": accepted,
        "changes": changes,
        "count": len(changes),
    }


def propose_add_note(record_kind: str, record_id: int, note: str) -> dict[str, object]:
    """Prepare a note on one opportunity or application. Never performs it.

    An application note is appended as its own event. An opportunity's notes
    are a single field the endpoint replaces, so the card shows both the
    existing text and the combined result - what will be stored is visible
    before the click.

    Compose the note yourself. Never paste raw recruiter email or web search
    text into it.
    """
    if record_kind not in _NOTE_KINDS:
        return {"error": f"Cannot add a note to '{record_kind}'.", "kinds": list(_NOTE_KINDS)}

    text = (note or "").strip()[:MAX_NOTE_CHARS]
    if not text:
        return {"hint": 'Ask the user for note. Do not guess.', "status": "missing_fields", "missing": ["note"]}

    db = SessionLocal()
    try:
        row = _load(db, record_kind, record_id)
        if row is None:
            return {"error": "Record not found", "record_id": int(record_id)}
        existing = (getattr(row, "notes", "") or "") if record_kind == "opportunity" else ""
        label = getattr(row, "job_title", None) or f"Record {record_id}"
    finally:
        db.close()

    payload: dict[str, object] = {
        "action": "propose_add_note",
        "record_kind": record_kind,
        "record_id": int(record_id),
        "record_label": str(label)[:120],
        "note": text,
        "append_only": record_kind == "application",
        # Composed by the assistant, possibly from untrusted recruiter or web
        # text. The card says so; _WEB_GUIDANCE forbids pasting raw source text.
        "authored_by": "assistant",
    }
    if record_kind == "application":
        payload["endpoint"] = f"/applications/{int(record_id)}/events"
        payload["event_type"] = "note"
    else:
        combined = f"{existing}\n\n{text}".strip() if existing else text
        payload["endpoint"] = f"/recruiter-opportunities/{int(record_id)}"
        payload["existing_notes"] = existing[:MAX_NOTE_CHARS]
        payload["combined_notes"] = combined[:MAX_NOTE_CHARS * 2]
        payload["replaces"] = True
    return payload
