from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import RecruiterEmail
from app import tenancy

MAX_IDS = 100
MAX_REASON_CHARS = 500

# action -> (endpoint, label, reversible, required state, extra body fields).
#
# `delete` is deliberately absent. It is irreversible, and the value of deleting
# candidate emails by voice is low against the cost of getting it wrong: a
# confirmation card protects against the model being wrong, not against a user
# misreading a card at speed. The Needs Review UI keeps it.
ACTIONS: dict[str, dict[str, object]] = {
    "reject": {
        "endpoint": "/candidates/reject-bulk",
        "label": "Reject",
        "reversible": False,
        "reversible_detail": "Rejected emails leave the review queue; there is no un-reject action.",
        "requires_state": "needs_review",
    },
    "track": {
        "endpoint": "/candidates/track-bulk",
        "label": "Mark for tracking",
        "reversible": True,
        "reversible_detail": "Reversible - you can stop tracking at any time.",
        "requires_state": None,
        "body": {"tracked": True},
    },
    "untrack": {
        "endpoint": "/candidates/track-bulk",
        "label": "Stop tracking",
        "reversible": True,
        "reversible_detail": "Reversible - you can mark for tracking again at any time.",
        "requires_state": None,
        "body": {"tracked": False},
    },
    "regenerate": {
        "endpoint": "/candidates/regenerate-bulk",
        "label": "Regenerate draft",
        "reversible": False,
        "reversible_detail": "Overwrites the current draft. Nothing is sent, but the previous draft is not kept.",
        "requires_state": None,
    },
    "send_to_failed_mapping": {
        "endpoint": "/candidates/send-to-failed-mapping-bulk",
        "label": "Send to Failed Mapping",
        "reversible": True,
        "reversible_detail": "Reversible - the email can be moved back out of Failed Mapping.",
        "requires_state": None,
    },
}


def propose_candidate_action(action: str, candidate_ids: list[int], reason: str = "") -> dict[str, object]:
    """Prepare a bulk action on candidate emails. Never performs it.

    Valid actions: reject, track, untrack, regenerate, send_to_failed_mapping.
    Pass the candidate ids you got from search_candidates or
    render_candidate_table.

    This returns a confirmation card. Only the user's click executes the
    action - never tell the user it has happened.
    """
    spec = ACTIONS.get(action)
    if spec is None:
        return {"error": f"Unknown action '{action}'.", "actions": sorted(ACTIONS)}

    requested = list(dict.fromkeys(int(value) for value in candidate_ids))[:MAX_IDS]
    dropped: list[dict[str, object]] = []

    db = SessionLocal()
    try:
        found = {
            row.id: row
            for row in db.query(RecruiterEmail).filter(
                RecruiterEmail.owner_id == tenancy.owner_id(),
                RecruiterEmail.id.in_(requested),
            )
        }
        eligible: list[int] = []
        roles: list[str] = []
        for candidate_id in requested:
            row = found.get(candidate_id)
            if row is None:
                # Out-of-scope and nonexistent give the same reason: telling
                # them apart would confirm another owner's id exists.
                dropped.append({"candidate_id": candidate_id, "reason": "not_found"})
                continue
            required = spec.get("requires_state")
            if required is not None and row.state != required:
                # Reported before the user clicks, not as N failures after.
                dropped.append({
                    "candidate_id": candidate_id,
                    "reason": "wrong_state",
                    "state": row.state,
                    "required_state": required,
                })
                continue
            eligible.append(candidate_id)
            if row.role:
                roles.append(row.role)
    finally:
        db.close()

    if not eligible:
        return {"hint": 'Ask the user for candidate_ids. Do not guess.', 
            "status": "missing_fields",
            "missing": ["candidate_ids"],
            "action": "propose_candidate_action",
            "candidate_action": action,
            "dropped": dropped,
        }

    payload: dict[str, object] = {
        "action": "propose_candidate_action",
        "candidate_action": action,
        "label": spec["label"],
        "endpoint": spec["endpoint"],
        "candidate_ids": eligible,
        "count": len(eligible),
        "roles": sorted(set(roles))[:10],
        "reason": (reason or "").strip()[:MAX_REASON_CHARS],
        "reversible": spec["reversible"],
        "reversible_detail": spec["reversible_detail"],
        "dropped": dropped,
    }
    body = spec.get("body")
    if isinstance(body, dict):
        payload.update(body)
    return payload
