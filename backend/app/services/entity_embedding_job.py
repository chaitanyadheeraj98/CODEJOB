"""Backfill `embedding_json` on approved canonical entities.

Mirrors `taxonomy_learning_service.embed_pending_skills` in shape - same batch
call, same ordering by occurrence, same idempotence - for entities rather than
skills. Production holds 1,780 entity entries with zero embeddings, so this is
a one-off backfill of roughly six batches plus a small recurring top-up, not a
continuous job.

The one behaviour that differs from the skills job: this one refuses to write a
hash embedding. `generate_embeddings` falls back to a deterministic hash when
sbert is unavailable and reports the fallback in its return value. Cosine
between two hash vectors is noise, and a resolver fed noise at a 0.90 bar will
still find something above the bar eventually. Rows are left `pending` instead,
so the backfill resumes correctly once a real provider is configured.
"""

from __future__ import annotations

from time import perf_counter

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CanonicalEntityTaxonomyEntry
from app.semantic.embeddings_service import embedding_to_json, generate_embeddings
from app.services.entity_resolution_service import RESOLVABLE_ENTITY_TYPES

EMBEDDED_STATUS = "done"
FAILED_STATUS = "failed"
PENDING_STATUS = "pending"


def _entity_text(row: CanonicalEntityTaxonomyEntry) -> str:
    return f"{row.canonical_name}. Entity type: {row.entity_type}."


def _pending_query(db: Session, *, owner_id: str, entity_type: str):
    return db.query(CanonicalEntityTaxonomyEntry).filter(
        CanonicalEntityTaxonomyEntry.owner_id == owner_id,
        CanonicalEntityTaxonomyEntry.entity_type == entity_type,
        CanonicalEntityTaxonomyEntry.status == "approved",
        CanonicalEntityTaxonomyEntry.embedding_status == PENDING_STATUS,
    )


def embed_pending_entities(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    batch_size: int = 300,
) -> dict[str, int | str]:
    """Embed one batch of pending canonical names for `entity_type`.

    Idempotent and resumable: a second call over an already-embedded set embeds
    zero, and a crash mid-batch leaves the unwritten rows `pending` because the
    commit is the last thing that happens.
    """
    started = perf_counter()
    if entity_type not in RESOLVABLE_ENTITY_TYPES:
        return {"embedded": 0, "failed": 0, "remaining": 0, "reason": f"unsupported entity type: {entity_type}"}

    rows = (
        _pending_query(db, owner_id=owner_id, entity_type=entity_type)
        .order_by(
            CanonicalEntityTaxonomyEntry.occurrence_count.desc(),
            CanonicalEntityTaxonomyEntry.id.asc(),
        )
        .limit(max(1, min(int(batch_size), 500)))
        .all()
    )
    if not rows:
        return {"embedded": 0, "failed": 0, "remaining": 0, "reason": "", "duration_ms": 0}

    if settings.effective_semantic_embedding_provider != "sbert":
        # Left pending, not failed: nothing is wrong with these rows, and the
        # backfill should pick them up unchanged once a provider exists.
        return {
            "embedded": 0,
            "failed": 0,
            "remaining": len(rows),
            "reason": "no semantic embedding provider configured; entities left pending",
            "duration_ms": int((perf_counter() - started) * 1000),
        }

    vectors, provider = generate_embeddings([_entity_text(row) for row in rows])
    if provider != "sbert" or len(vectors) != len(rows):
        return {
            "embedded": 0,
            "failed": 0,
            "remaining": len(rows),
            "reason": f"embedding provider fell back to {provider}; refusing to store hash vectors",
            "duration_ms": int((perf_counter() - started) * 1000),
        }

    embedded = 0
    failed = 0
    for row, vector in zip(rows, vectors):
        if vector:
            row.embedding_json = embedding_to_json(vector)
            row.embedding_status = EMBEDDED_STATUS
            embedded += 1
        else:
            # Never leave a row pending after a successful provider call - it
            # would be retried forever at the front of the queue.
            row.embedding_status = FAILED_STATUS
            failed += 1
    db.commit()

    return {
        "embedded": embedded,
        "failed": failed,
        "remaining": _pending_query(db, owner_id=owner_id, entity_type=entity_type).count(),
        "reason": "",
        "duration_ms": int((perf_counter() - started) * 1000),
    }


def embed_pending_entities_all_types(
    db: Session, *, owner_id: str, batch_size: int = 300
) -> dict[str, dict[str, int | str]]:
    """Run one batch for each resolvable entity type. The sweep's entry point."""
    return {
        entity_type: embed_pending_entities(
            db, owner_id=owner_id, entity_type=entity_type, batch_size=batch_size
        )
        for entity_type in RESOLVABLE_ENTITY_TYPES
    }
