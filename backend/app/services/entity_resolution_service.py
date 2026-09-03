"""Resolve a surface form to an approved canonical entity.

Order is exact, then alias, then semantic, then unresolved - deliberately the
same order `mcp_server/tools/references.resolve_record_reference` already uses,
so the codebase has one matching idiom rather than two.

Two things this module does *not* do, both on purpose.

It does not build a second alias matcher. `role_taxonomy.load_entity_taxonomy`
is already a cached, word-bounded `AliasMatcher` over `aliases_json` for exactly
the three entity types worth resolving, with per-type rules that took real
tuning (a single-word role is a technology, not a job title). A second matcher
here would drift from that one, and two components disagreeing about whether
two names are the same entity is the failure this phase exists to prevent.

It does not accept a hash embedding as semantic evidence. `generate_embeddings`
silently falls back to a hash when sbert is unavailable, and cosine between two
hash vectors is noise. A wrong company merge propagates into every cluster that
entity touches, so the semantic branch is switched off entirely rather than fed
vectors that cannot mean anything. `role_taxonomy`'s own docstring makes the
same argument: embeddings are an optional enhancement on top of the lexical
vocabulary, never the mechanism.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CanonicalEntityTaxonomyEntry
from app.semantic.embeddings_service import embedding_from_json, generate_embeddings
from app.semantic.similarity import cosine_similarity
from app.services.role_taxonomy import ENTITY_CONFIG, clear_role_taxonomy_cache, load_entity_taxonomy
from app.skill_taxonomy import normalize_taxonomy_text

# The entity types W3 covers. End client and implementation partner are absent
# on purpose: at 6.7% and 1.6% production coverage there is nothing to resolve,
# and resolving a near-empty column would produce confident-looking canonical
# names over almost no data.
RESOLVABLE_ENTITY_TYPES = tuple(ENTITY_CONFIG)

# Corporate suffixes carry no identity. "Horizon Softech Inc" and "Horizon
# Softech LLC" are the same firm; stripping is only ever used to *suggest* a
# merge for review, never to apply one.
_SUFFIXES = (
    "inc", "inc.", "llc", "l.l.c", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "gmbh", "pvt", "private", "technologies",
    "technology solutions", "solutions", "systems", "services", "group",
    "consulting", "consultancy", "software", "softech", "labs",
)

_SUFFIX_RE = re.compile(r"\b(" + "|".join(re.escape(item) for item in _SUFFIXES) + r")\b")

MATCH_EXACT = "exact"
MATCH_ALIAS = "alias"
MATCH_SEMANTIC = "semantic"
MATCH_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class ResolvedEntity:
    """What a surface form resolved to, and how.

    `canonical` is the surface itself when unresolved. Never invent a canonical
    name: an unresolved surface is a real answer, and substituting the nearest
    entry is how a resolver starts merging things that are not the same.
    """

    surface: str
    canonical: str
    entry_id: int | None
    match: str
    score: float

    @property
    def resolved(self) -> bool:
        return self.match != MATCH_UNRESOLVED


@dataclass(frozen=True)
class _Index:
    """One pass's view of an entity type. Built once, queried in memory."""

    by_normalized_canonical: dict[str, tuple[int, str]]
    alias_lookup: dict[str, str]
    matcher: object
    canonical_to_id: dict[str, int]
    embeddings: dict[str, list[float]]

    @property
    def has_embeddings(self) -> bool:
        return bool(self.embeddings)


def strip_corporate_suffixes(value: str) -> str:
    """Normalize away legal-form noise. Suggestion-only input, never stored."""
    normalized = normalize_taxonomy_text(value)
    stripped = _SUFFIX_RE.sub(" ", normalized)
    return re.sub(r"\s+", " ", stripped).strip() or normalized


def _semantic_enabled() -> bool:
    # A hash-provider deployment gets the lexical branches and nothing else.
    return settings.effective_semantic_embedding_provider == "sbert"


def _aliases(row: CanonicalEntityTaxonomyEntry) -> list[str]:
    try:
        parsed = json.loads(row.aliases_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def build_index(db: Session, *, owner_id: str, entity_type: str) -> _Index:
    """One query per (owner_id, entity_type). W5 calls this once per pass.

    603,801 candidate opportunity pairs makes a per-pair query impossible, so
    the batching here is a correctness requirement rather than a speed-up.
    """
    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
            CanonicalEntityTaxonomyEntry.status == "approved",
        )
        .order_by(CanonicalEntityTaxonomyEntry.id.asc())
        .all()
    )

    by_normalized_canonical: dict[str, tuple[int, str]] = {}
    canonical_to_id: dict[str, int] = {}
    embeddings: dict[str, list[float]] = {}
    for row in rows:
        canonical = str(row.canonical_name or "").strip()
        if not canonical:
            continue
        normalized = normalize_taxonomy_text(canonical)
        if normalized:
            by_normalized_canonical.setdefault(normalized, (int(row.id), canonical))
        canonical_to_id.setdefault(canonical, int(row.id))
        if _semantic_enabled():
            vector = embedding_from_json(row.embedding_json)
            if vector:
                embeddings[canonical] = vector

    taxonomy = load_entity_taxonomy(db, owner_id=owner_id, entity_type=entity_type)
    return _Index(
        by_normalized_canonical=by_normalized_canonical,
        alias_lookup=taxonomy.lookup,
        matcher=taxonomy.matcher,
        canonical_to_id=canonical_to_id,
        embeddings=embeddings,
    )


def _alias_match(index: _Index, normalized: str) -> str | None:
    """Longest word-bounded approved alias inside the surface, or None.

    Longest wins for the same reason it does in `role_taxonomy._best_match`:
    "Java Full Stack Developer" must beat "Java Developer" when both are
    approved.
    """
    if not index.alias_lookup:
        return None
    direct = index.alias_lookup.get(normalized)
    if direct:
        return direct
    best: str | None = None
    best_length = 0
    for match in index.matcher.find(normalized):  # type: ignore[attr-defined]
        if len(match.alias) > best_length:
            best_length = len(match.alias)
            best = index.alias_lookup.get(match.alias)
    return best


def _lexical(index: _Index, surface: str) -> ResolvedEntity | None:
    normalized = normalize_taxonomy_text(surface)
    if not normalized:
        return ResolvedEntity(surface=surface, canonical=surface, entry_id=None, match=MATCH_UNRESOLVED, score=0.0)
    exact = index.by_normalized_canonical.get(normalized)
    if exact:
        return ResolvedEntity(surface=surface, canonical=exact[1], entry_id=exact[0], match=MATCH_EXACT, score=1.0)
    alias = _alias_match(index, normalized)
    if alias:
        return ResolvedEntity(
            surface=surface, canonical=alias, entry_id=index.canonical_to_id.get(alias), match=MATCH_ALIAS, score=1.0
        )
    return None


def _unresolved(surface: str) -> ResolvedEntity:
    return ResolvedEntity(surface=surface, canonical=surface, entry_id=None, match=MATCH_UNRESOLVED, score=0.0)


def _semantic(index: _Index, surface: str, vector: list[float], min_semantic: float) -> ResolvedEntity:
    best_canonical: str | None = None
    best_score = 0.0
    for canonical, candidate in index.embeddings.items():
        score = cosine_similarity(vector, candidate)
        if score > best_score:
            best_score, best_canonical = score, canonical
    if best_canonical is None or best_score < min_semantic:
        # Below the bar is unresolved, never "the nearest entry". A nearest
        # neighbour is always available and is exactly how a resolver merges
        # two firms that merely sound alike.
        return _unresolved(surface)
    return ResolvedEntity(
        surface=surface,
        canonical=best_canonical,
        entry_id=index.canonical_to_id.get(best_canonical),
        match=MATCH_SEMANTIC,
        score=round(best_score, 4),
    )


def resolve_many(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    surfaces: list[str],
    min_semantic: float = 0.90,
    index: _Index | None = None,
) -> dict[str, ResolvedEntity]:
    """Resolve a batch of surfaces with one query and at most one embed call."""
    resolved: dict[str, ResolvedEntity] = {}
    unique = [item for item in dict.fromkeys(surfaces) if item is not None]
    if not unique:
        return resolved
    working = index if index is not None else build_index(db, owner_id=owner_id, entity_type=entity_type)

    pending: list[str] = []
    for surface in unique:
        lexical = _lexical(working, surface)
        if lexical is not None:
            resolved[surface] = lexical
        else:
            pending.append(surface)

    if not pending:
        return resolved
    if not (_semantic_enabled() and working.has_embeddings):
        for surface in pending:
            resolved[surface] = _unresolved(surface)
        return resolved

    vectors, provider = generate_embeddings(pending)
    if provider != "sbert" or len(vectors) != len(pending):
        # The batch fell back mid-flight. Noise is not evidence.
        for surface in pending:
            resolved[surface] = _unresolved(surface)
        return resolved
    for surface, vector in zip(pending, vectors):
        resolved[surface] = _semantic(working, surface, vector, min_semantic)
    return resolved


def resolve_entity(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    surface: str,
    min_semantic: float = 0.90,
) -> ResolvedEntity:
    """Single-surface convenience. Prefer `resolve_many` inside a scoring pass."""
    return resolve_many(
        db, owner_id=owner_id, entity_type=entity_type, surfaces=[surface], min_semantic=min_semantic
    ).get(surface, _unresolved(surface))


def suggest_aliases(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    min_similarity: float = 0.94,
) -> list[tuple[int, int, float]]:
    """Pairs of approved entries that are probably the same entity.

    Returns (keep_id, alias_id, score) and **writes nothing**. A wrong merge is
    invisible and permanent, so the decision belongs to a person: these
    suggestions surface in the labeling tool's alias-review mode.

    The larger `occurrence_count` is kept, ties broken by the lower id, so the
    suggestion is stable between runs and a reviewer sees the same pair the
    same way round each time.
    """
    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
            CanonicalEntityTaxonomyEntry.status == "approved",
        )
        .order_by(CanonicalEntityTaxonomyEntry.id.asc())
        .all()
    )
    known_aliases = {
        (int(row.id), normalize_taxonomy_text(alias)) for row in rows for alias in _aliases(row)
    }
    vectors = {
        int(row.id): embedding_from_json(row.embedding_json)
        for row in rows
        if _semantic_enabled() and row.embedding_json
    }

    suggestions: list[tuple[int, int, float]] = []
    for position, left in enumerate(rows):
        for right in rows[position + 1 :]:
            left_stripped = strip_corporate_suffixes(left.canonical_name or "")
            right_stripped = strip_corporate_suffixes(right.canonical_name or "")
            if not left_stripped or not right_stripped:
                continue
            score = 0.0
            if left_stripped == right_stripped:
                # Identical once the legal form is removed. This is the case the
                # production data is full of, and it needs no embedding at all.
                score = 1.0
            else:
                left_vector, right_vector = vectors.get(int(left.id)), vectors.get(int(right.id))
                if left_vector and right_vector:
                    score = cosine_similarity(left_vector, right_vector)
            if score < min_similarity:
                continue
            keep, drop = (left, right)
            if (int(right.occurrence_count or 0), -int(right.id)) > (int(left.occurrence_count or 0), -int(left.id)):
                keep, drop = (right, left)
            if (int(keep.id), normalize_taxonomy_text(drop.canonical_name or "")) in known_aliases:
                continue
            suggestions.append((int(keep.id), int(drop.id), round(float(score), 4)))
    suggestions.sort(key=lambda item: (-item[2], item[0], item[1]))
    return suggestions


def apply_alias_merge(db: Session, *, owner_id: str, entity_type: str, keep_id: int, alias_id: int) -> dict[str, object]:
    """Fold one reviewed entry into another as an alias.

    The only writer of `aliases_json` in the codebase, and it runs only from a
    human decision in the labeling tool's alias-review mode. The dropped entry
    is marked `dismissed` rather than deleted: the merge stays auditable, and
    `load_entity_taxonomy` already loads approved entries only, so a dismissed
    row leaves the vocabulary on its own.
    """
    if keep_id == alias_id:
        raise ValueError("an entry cannot be an alias of itself")
    rows = {
        int(row.id): row
        for row in db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
            CanonicalEntityTaxonomyEntry.id.in_((keep_id, alias_id)),
        )
        .all()
    }
    keep, drop = rows.get(keep_id), rows.get(alias_id)
    if keep is None or drop is None:
        raise LookupError("both entries must exist for this owner and entity type")

    aliases = _aliases(keep)
    for surface in [drop.canonical_name, *_aliases(drop)]:
        text = str(surface or "").strip()
        if text and text not in aliases and normalize_taxonomy_text(text) != normalize_taxonomy_text(keep.canonical_name or ""):
            aliases.append(text)
    keep.aliases_json = json.dumps(aliases)
    keep.occurrence_count = int(keep.occurrence_count or 0) + int(drop.occurrence_count or 0)
    drop.status = "dismissed"
    db.commit()
    # The lexical vocabulary is cached per (owner_id, entity_type); a merge that
    # does not clear it leaves the app matching against the pre-merge names.
    clear_role_taxonomy_cache()
    return {"keep_id": keep_id, "alias_id": alias_id, "aliases": aliases}
