from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.services.taxonomy_bulk_review_service import (
    BUCKET_APPROVE,
    BUCKET_DISMISS,
    SKILL_SCOPE,
    SCOPES,
    bucket_counts,
    classify_entities,
    classify_skills,
    refine_with_model,
)
from app.services.taxonomy_learning_service import list_pending_entities
from app import tenancy


# The card carries the exact keys it will write, so the list has to stay small
# enough to sit in one tool result. 200 at a time drains 1,647 pending skills in
# nine confirmations instead of 1,647 clicks, and every one of the nine is a real
# confirmation of a real set rather than a blanket "approve all".
MAX_PROPOSAL_KEYS = 200
SAMPLE_NAMES = 15

ACTIONS = {
    BUCKET_APPROVE: {
        "label": "Approve",
        "reversible": True,
        "reversible_detail": "Reversible - an approved value can be dismissed later from Settings.",
    },
    BUCKET_DISMISS: {
        "label": "Dismiss",
        "reversible": True,
        "reversible_detail": "Reversible - a dismissed value can be approved later from Settings.",
    },
}


def propose_taxonomy_bulk_review(
    scope: str,
    action: str,
    use_model: bool = True,
) -> dict[str, object]:
    """Prepare a bulk approve or dismiss of pending taxonomy values. Never performs it.

    scope is one of skill, company, location, role. action is approve or dismiss.
    The server classifies every pending record itself and this tool proposes only
    the records its own rules put in that bucket - you do not choose which values
    are included, and you cannot add one. Values the classifier could not decide
    are never proposed; they need the Bulk review screen in Settings.

    Set use_model=true to let DeepSeek re-sort the undecided middle first.

    This returns a confirmation card. Only the user's click executes the action -
    never tell the user it has happened.
    """
    normalized_scope = (scope or "").strip().lower()
    normalized_action = (action or "").strip().lower()
    if normalized_scope not in SCOPES:
        return {"error": f"Unknown scope '{scope}'.", "scopes": sorted(SCOPES)}
    if normalized_action not in ACTIONS:
        return {"error": f"Unknown action '{action}'.", "actions": sorted(ACTIONS)}

    db = SessionLocal()
    try:
        if normalized_scope == SKILL_SCOPE:
            # Imported here, not at module scope: the pending-skill reader lives in
            # main.py, which imports this package's tools at startup.
            from app.main import _list_pending_unknown_skills

            recommendations = classify_skills(_list_pending_unknown_skills(db))
        else:
            recommendations = classify_entities(
                list_pending_entities(db, owner_id=tenancy.owner_id(), entity_type=normalized_scope)
            )
    finally:
        db.close()

    model_error: str | None = None
    if use_model and settings.deepseek_api_key:
        recommendations, model_error = refine_with_model(recommendations, scope=normalized_scope)
    elif use_model:
        model_error = "DeepSeek API key is missing; using rules-only recommendations."

    counts = bucket_counts(recommendations)
    selected = sorted(
        (item for item in recommendations if item.bucket == normalized_action),
        key=lambda item: (-item.occurrence_count, item.display_name.casefold()),
    )
    # Belt and braces. A locked record is only ever in the dismiss bucket, so this
    # cannot fire today - it is here so that stops being a thing to remember if the
    # bucketing rules ever change.
    if normalized_action == BUCKET_APPROVE:
        selected = [item for item in selected if not item.locked]

    batch = selected[:MAX_PROPOSAL_KEYS]
    if not batch:
        return {
            "status": "nothing_to_do",
            "scope": normalized_scope,
            "taxonomy_action": normalized_action,
            "counts": counts,
            "message": (
                f"Nothing is bucketed as {normalized_action} for {normalized_scope}. "
                f"{counts.get('review', 0)} record(s) need a human in the Bulk review screen."
            ),
        }

    spec = ACTIONS[normalized_action]
    return {
        "action": "propose_taxonomy_bulk_review",
        "scope": normalized_scope,
        "taxonomy_action": normalized_action,
        "label": spec["label"],
        "keys": [item.key for item in batch],
        "count": len(batch),
        "sample_names": [item.display_name for item in batch[:SAMPLE_NAMES]],
        "total_in_bucket": len(selected),
        "remaining_after_batch": max(0, len(selected) - len(batch)),
        "needs_human_count": counts.get("review", 0),
        "reversible": spec["reversible"],
        "reversible_detail": spec["reversible_detail"],
        "model_error": model_error,
    }
