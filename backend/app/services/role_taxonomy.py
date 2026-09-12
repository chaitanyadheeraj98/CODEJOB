"""Lexical vocabulary matching over human-approved taxonomy entries.

This is the entity equivalent of `skill_taxonomy.extract_taxonomy_skills`, and it
is deliberately lexical rather than embedding-based.

The point of curating a vocabulary is that the parser keeps working when AI does
not. `feature_ai_enabled` is off on this deployment today, and skill extraction
still works because `load_skill_taxonomy()` merges approved entries into a
word-bounded alias matcher needing no model, no network and no API key. An
embedding matcher would fail in exactly the situation the curation exists for, so
embeddings can only ever be an optional enhancement on top of this - useful for
near-misses ("Sr. Java Backend Engineer" vs "Java Developer"), never the mechanism.

Note what this does and does not do. It *matches* strings against an approved
vocabulary; it does not *extract* them. Embeddings would not change that either -
finding which span of a document is a location is span labelling (NER), a
different model class from sentence embeddings. The rules-based parser remains the
floor: if it isolates no candidate, no vocabulary can rescue it.

Only `status == "approved"` entries load. Pending is a proposal nobody accepted;
dismissed is junk somebody explicitly rejected. Neither is vocabulary the parser
should trust - which is exactly why manual review is worth the effort.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.base_taxonomy import base_entity_entries, clear_base_taxonomy_cache
from app.models import CanonicalEntityTaxonomyEntry
from app.services.role_provenance import RoleSource, TaxonomyMatch
from app.skill_taxonomy import normalize_taxonomy_text
from app.taxonomy_matcher import AliasMatcher

ROLE_ENTITY_TYPE = "role"
COMPANY_ENTITY_TYPE = "company"
LOCATION_ENTITY_TYPE = "location"


@dataclass(frozen=True)
class EntityTypeConfig:
    """Per-type matching rules. The differences are not cosmetic."""

    min_alias_words: int
    body_scan_chars: int


ENTITY_CONFIG: dict[str, EntityTypeConfig] = {
    # A single-word role is a technology, not a job title: an approved "Java"
    # would match every email that mentions the stack.
    ROLE_ENTITY_TYPE: EntityTypeConfig(min_alias_words=2, body_scan_chars=600),
    # Company and location names are legitimately one word - "ADP", "Austin",
    # "Remote" - so the two-word floor cannot apply here.
    COMPANY_ENTITY_TYPE: EntityTypeConfig(min_alias_words=1, body_scan_chars=600),
    # Half the window company gets. A posting names several cities in passing -
    # eligibility, client sites, 'or remote' - so the further into the body a match
    # is found, the less likely it is to be *this* job's location.
    LOCATION_ENTITY_TYPE: EntityTypeConfig(min_alias_words=1, body_scan_chars=300),
}


@dataclass(frozen=True)
class EntityTaxonomy:
    """Normalized alias -> canonical name, plus a matcher over those aliases."""

    lookup: dict[str, str]
    matcher: AliasMatcher

    @property
    def size(self) -> int:
        return len(self.lookup)


# Keyed by (owner_id, entity_type).
_CACHE: dict[tuple[str, str], EntityTaxonomy] = {}


def clear_role_taxonomy_cache() -> None:
    """Drop every cached vocabulary. Called on approve / approve-all / dismiss.

    Named for roles historically; it clears all entity types, because one handler
    serves all three and a stale company vocabulary is just as wrong as a stale
    role one.
    """
    _CACHE.clear()
    clear_base_taxonomy_cache()


def _aliases(row: CanonicalEntityTaxonomyEntry) -> list[str]:
    try:
        parsed = json.loads(row.aliases_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def load_entity_taxonomy(db: Session, *, owner_id: str, entity_type: str) -> EntityTaxonomy:
    key = (owner_id, entity_type)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    config = ENTITY_CONFIG.get(entity_type)
    if config is None:
        taxonomy = EntityTaxonomy(lookup={}, matcher=AliasMatcher([]))
        _CACHE[key] = taxonomy
        return taxonomy

    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
        )
        # Newest first, so the *latest* decision wins below. The unique
        # constraint is on the raw `canonical_name`, not the normalized one, so
        # "Java Developer" and "java developer" are two permitted rows that
        # collapse to one key here - and a user who dismissed a name and later
        # approved it must get the approval, not the dismissal.
        .order_by(CanonicalEntityTaxonomyEntry.id.desc())
        .all()
    )

    decisions: dict[str, CanonicalEntityTaxonomyEntry] = {}
    for row in rows:
        normalized = normalize_taxonomy_text(row.canonical_name)
        if normalized and (row.suppressed or row.status in {"approved", "dismissed"}):
            decisions.setdefault(normalized, row)

    entries: list[tuple[str, list[str]]] = [
        (str(row.canonical_name or "").strip(), _aliases(row))
        for row in decisions.values()
        if row.status == "approved" and not row.suppressed
    ]
    entries.extend(
        (entry.canonical_name, list(entry.aliases))
        for entry in base_entity_entries(entity_type)
        if normalize_taxonomy_text(entry.canonical_name) not in decisions
    )

    lookup: dict[str, str] = {}
    for canonical, aliases in entries:
        if not canonical:
            continue
        for surface in [canonical, *aliases]:
            normalized = normalize_taxonomy_text(surface)
            if not normalized or len(normalized.split()) < config.min_alias_words:
                continue
            # First writer wins, so a canonical name is never shadowed by another
            # entry's alias for the same string.
            lookup.setdefault(normalized, canonical)

    taxonomy = EntityTaxonomy(lookup=lookup, matcher=AliasMatcher(list(lookup)))
    _CACHE[key] = taxonomy
    return taxonomy


def load_role_taxonomy(db: Session, *, owner_id: str) -> EntityTaxonomy:
    return load_entity_taxonomy(db, owner_id=owner_id, entity_type=ROLE_ENTITY_TYPE)


def _best_match(taxonomy: EntityTaxonomy, text: str | None) -> str | None:
    """Longest word-bounded alias in `text`, or None.

    Longest wins so "Java Full Stack Developer" beats "Java Developer", and
    "Dallas, TX" beats "Dallas", when both are approved.
    """
    normalized = normalize_taxonomy_text(text)
    if not normalized or not taxonomy.lookup:
        return None
    best: str | None = None
    best_length = 0
    for match in taxonomy.matcher.find(normalized):
        length = len(match.alias)
        if length > best_length:
            best_length = length
            best = taxonomy.lookup.get(match.alias)
    return best


def match_entity_from_taxonomy(
    db: Session,
    *,
    owner_id: str,
    entity_type: str,
    text: str | None,
    body: str | None = None,
) -> str | None:
    """An approved name found in `text`, else near the top of `body`, else None.

    Returning None is the safe outcome: callers must fall through rather than
    invent a value. Unlike a nearest-neighbour search there is no "closest entry"
    to be tempted by - a lexical miss really is a miss.

    The body is scanned only near the top because a name buried in a benefits
    paragraph or an email footer is not this posting's company or location.
    """
    taxonomy = load_entity_taxonomy(db, owner_id=owner_id, entity_type=entity_type)
    if not taxonomy.lookup:
        return None
    config = ENTITY_CONFIG[entity_type]
    found = _best_match(taxonomy, text)
    if found is None and body:
        found = _best_match(taxonomy, body[: config.body_scan_chars])
    return found


def match_role_from_taxonomy(
    db: Session, *, owner_id: str, text: str | None, body: str | None = None
) -> TaxonomyMatch | None:
    canonical = match_entity_from_taxonomy(
        db, owner_id=owner_id, entity_type=ROLE_ENTITY_TYPE, text=text, body=body
    )
    return TaxonomyMatch(canonical) if canonical else None


def role_matcher_for(db: Session, owner_id: str):
    """Bind a role matcher to a session for `role_provenance.assign_role`."""

    def _match(text: str, body: str) -> TaxonomyMatch | None:
        return match_role_from_taxonomy(db, owner_id=owner_id, text=text, body=body)

    return _match


# The parser's own words for "I found nothing". Treated as empty so the taxonomy
# can fill them, rather than as a real answer that must be preserved.
LOCATION_PLACEHOLDERS = {"", "unknown", "not_specified", "none", "n/a", "-"}


def fill_entity_gaps(
    fields: dict[str, str | None],
    *,
    db: Session,
    owner_id: str,
    subject: str | None,
    body: str | None,
    location: str | None = None,
) -> dict[str, str | None]:
    """Fill `company` and `location` from the approved vocabulary - only where empty.

    Strictly additive: a value the parser produced is never overwritten. The
    taxonomy is a floor for what the base parser can know without AI, not a second
    opinion competing with extraction.

    Every outcome is labelled (`company_source` / `location_source`), not just the
    fills. Labelling only the fills made NULL mean two opposite things - "the
    parser found this" and "there is nothing here" - which inverted the column's
    meaning in practice: of 39 rows ingested after provenance shipped, 30 carried a
    real company and none were labelled, while the single labelled location was the
    two-letter fragment "IN" that the taxonomy had filled. A consumer asking for
    verified values got the weakest row and discarded the other 38.

    So an extracted value is now marked `extracted` and a filled one stays
    `taxonomy_matched`. NULL narrows to one meaning: no value. The label records
    where a value came from, never that it is correct.

    Location is handled more cautiously than company: it is empty on only 13% of
    rows (company: 82%), and city names appear throughout a posting for reasons
    that have nothing to do with where the job is. Hence the shorter body window
    in ENTITY_CONFIG, and the placeholder list below - the parser's "unknown"
    counts as a gap, not as an answer worth protecting.

    `location` is passed in rather than read from `fields` because it is not part
    of JD_ENTITY_FIELDS; every call site writes it from its own parsed value.
    """
    filled = dict(fields)

    if (filled.get("company") or "").strip():
        filled["company_source"] = RoleSource.EXTRACTED
    else:
        match = match_entity_from_taxonomy(
            db, owner_id=owner_id, entity_type=COMPANY_ENTITY_TYPE, text=subject, body=body
        )
        if match:
            filled["company"] = match
            filled["company_source"] = RoleSource.TAXONOMY_MATCHED

    current = (location or "").strip()
    filled["location"] = current
    # A placeholder is the parser saying "nothing here", so it is a gap to fill and
    # never an extraction to label - otherwise "unknown" would be marked verified.
    if current.lower() not in LOCATION_PLACEHOLDERS:
        filled["location_source"] = RoleSource.EXTRACTED
    else:
        match = match_entity_from_taxonomy(
            db, owner_id=owner_id, entity_type=LOCATION_ENTITY_TYPE, text=subject, body=body
        )
        if match:
            filled["location"] = match
            filled["location_source"] = RoleSource.TAXONOMY_MATCHED

    return filled
