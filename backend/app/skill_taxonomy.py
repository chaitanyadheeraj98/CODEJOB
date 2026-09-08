from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Protocol

from app.taxonomy_matcher import AliasMatcher

_WORD_RE = re.compile(r"[^a-z0-9]+")
_SKILL_SPLIT_RE = re.compile(r"[,;\n]+")
TAXONOMY_PLACEHOLDER_KEYS = frozenset({"none detected", "unknown", "not specified", "n a"})
_ROLE_FAMILY_AI_RE = re.compile(
    r"\b(ai|artificial intelligence|genai|llm|machine learning|ml|prompt)\b",
    re.IGNORECASE,
)
_ROLE_FAMILY_JAVA_FULLSTACK_RE = re.compile(r"\b(full stack|fullstack)\b", re.IGNORECASE)
_ROLE_FAMILY_JAVA_BACKEND_RE = re.compile(r"\b(java|spring|backend|microservices?)\b", re.IGNORECASE)
_ROLE_FAMILY_FRONTEND_RE = re.compile(r"\b(frontend|front end|ui|react|angular|typescript)\b", re.IGNORECASE)
_ROLE_FAMILY_DEVOPS_RE = re.compile(r"\b(devops|sre|platform|cloud|kubernetes|docker|aws)\b", re.IGNORECASE)
_ROLE_FAMILY_DATA_RE = re.compile(r"\b(data|etl|analytics|bi|warehouse|pipeline)\b", re.IGNORECASE)
_FALLBACK_WEIGHTS = {
    "language": 1.0,
    "backend": 1.0,
    "framework": 1.0,
    "architecture": 1.05,
    "database": 0.95,
    "devops": 0.95,
}
_FALLBACK_SKILLS = [
    ("Python", ["python"], "language"),
    ("FastAPI", ["fastapi"], "backend"),
    ("SQL", ["sql"], "language"),
    ("PostgreSQL", ["postgres", "postgresql"], "database"),
    ("SQLite", ["sqlite"], "database"),
    ("React", ["react"], "frontend"),
    ("TypeScript", ["typescript"], "language"),
    ("Java", ["java"], "language"),
    ("Spring Framework", ["spring"], "framework"),
    ("Spring Boot", ["spring boot", "springboot"], "framework"),
    ("Microservices", ["microservices"], "architecture"),
    ("Kafka", ["kafka"], "messaging"),
    ("AWS", ["aws"], "cloud"),
    ("Docker", ["docker"], "devops"),
]
_STRONG_EVIDENCE_BUCKETS = {"mandatory", "required", "technical_skills", "essential"}
_WEAK_EVIDENCE_BUCKETS = {"preferred", "summary", "unknown"}
_FOUNDATION_CATEGORIES = {
    "language",
    "backend",
    "framework",
    "architecture",
    "database",
    "devops",
    "cloud",
    "testing",
    "security",
    "observability",
    "messaging",
}
_MATCH_GUARDS: dict[str, dict[str, object]] = {
    "safe": {
        "blocked_single_token": True,
        "allowed_phrases": {"scaled agile framework"},
    },
    "services": {
        "blocked_single_token": True,
        "allowed_phrases": {"angular services", "rest services", "soap services"},
    },
}
_ROLE_FAMILY_CONFIG: dict[str, dict[str, object]] = {
    "ai": {
        "preferred_clusters": {
            "retrieval_rag",
            "agentic_workflows",
            "safe_responsible_ai",
            "ai_operations",
            "compliance_security",
            "ai_runtime",
        },
        "preferred_categories": {
            "ai_data",
            "ai",
            "observability",
            "security",
            "testing",
            "language",
            "backend",
            "framework",
            "architecture",
            "cloud",
            "database",
            "messaging",
        },
        "suppress_categories": {"frontend", "methodology", "domain_banking", "domain_healthcare", "operations"},
        "suppress_skills": {"angular services", "safe"},
    },
    "java_fullstack": {
        "preferred_clusters": {"production_engineering"},
        "preferred_categories": {
            "language",
            "backend",
            "framework",
            "frontend",
            "database",
            "devops",
            "cloud",
            "observability",
            "testing",
            "security",
            "messaging",
        },
        "suppress_categories": set(),
        "suppress_skills": set(),
    },
    "java_backend": {
        "preferred_clusters": {"production_engineering"},
        "preferred_categories": {
            "language",
            "backend",
            "framework",
            "database",
            "devops",
            "cloud",
            "observability",
            "testing",
            "security",
            "messaging",
        },
        "suppress_categories": {"frontend"},
        "suppress_skills": set(),
    },
    "frontend": {
        "preferred_clusters": {"production_engineering"},
        "preferred_categories": {
            "frontend",
            "language",
            "backend",
            "testing",
            "observability",
            "security",
        },
        "suppress_categories": {"domain_banking"},
        "suppress_skills": set(),
    },
    "devops_cloud": {
        "preferred_clusters": {"production_engineering"},
        "preferred_categories": {
            "devops",
            "cloud",
            "observability",
            "security",
            "backend",
            "architecture",
            "messaging",
            "testing",
        },
        "suppress_categories": {"frontend"},
        "suppress_skills": set(),
    },
    "data": {
        "preferred_clusters": {"production_engineering"},
        "preferred_categories": {
            "data",
            "database",
            "language",
            "backend",
            "observability",
            "cloud",
            "security",
        },
        "suppress_categories": {"frontend"},
        "suppress_skills": set(),
    },
    "general": {
        "preferred_clusters": set(),
        "preferred_categories": set(),
        "suppress_categories": set(),
        "suppress_skills": set(),
    },
}


@dataclass(frozen=True)
class SkillTaxonomyEntry:
    id: str
    canonical_name: str
    category: str
    weight: float
    aliases: tuple[str, ...]
    normalized_forms: tuple[str, ...]
    cluster_id: str
    related_skill_ids: tuple[str, ...]
    match_tier: str
    intent_clusters: tuple[str, ...]
    strength_signals: tuple[str, ...]
    weak_signals: tuple[str, ...]


@dataclass(frozen=True)
class SkillTaxonomy:
    entries: tuple[SkillTaxonomyEntry, ...]
    entries_for_search: tuple[SkillTaxonomyEntry, ...]
    exact_lookup: dict[str, SkillTaxonomyEntry]
    validation_warnings: tuple[str, ...] = ()
    ambiguous_aliases: frozenset[str] = frozenset()


@dataclass(frozen=True)
class TaxonomySkillMatch:
    entry: SkillTaxonomyEntry
    matched_alias: str
    start: int
    end: int
    normalized_text: str


@dataclass(frozen=True)
class CustomSkillSeed:
    canonical_name: str
    aliases: tuple[str, ...]
    category: str
    cluster_hint: str | None = None
    description: str = ""
    weight: float = 1.0
    match_tier: str = "supporting"
    occurrence_count: int = 0
    owner_id: str = "default-owner"
    status: str = "approved"


@dataclass(frozen=True)
class IntentMatchBreakdown:
    score: float
    specialization_score: float
    foundation_score: float
    role_alignment_score: float
    matched_specialization_skills: tuple[str, ...]
    missing_specialization_skills: tuple[str, ...]
    matched_clusters: tuple[str, ...]
    weak_signal_hits: tuple[str, ...]
    jd_role_family: str
    resume_role_family: str


@dataclass(frozen=True)
class RoleFamilyClassification:
    """A role family with the evidence behind it, not just the answer.

    `detect_role_family` returns a bare string, so nothing downstream can tell a
    confident title match apart from a photo-finish between two families that
    happened to land one weight apart. Persisting `confidence` alongside `family`
    is what lets an aggregate hold the uncertain rows out instead of blending them
    in - see services/role_gap_service.py.

    `taxonomy_version` records which vocabulary produced the answer, so swapping in
    an external occupation standard later is a value change (`onet:29.1`) rather
    than another migration.
    """

    family: str
    confidence: float
    method: str
    taxonomy_version: str


class JDSectionLike(Protocol):
    heading: str
    bucket: str
    weight: float
    text: str


@dataclass(frozen=True)
class SkillEvidence:
    skill_id: str
    canonical_name: str
    bucket: str
    section_heading: str
    section_weight: float
    skill_weight: float
    evidence_text: str
    first_position: int


@dataclass(frozen=True)
class AggregatedSkill:
    skill_id: str
    canonical_name: str
    total_weight: float
    buckets: tuple[str, ...]
    section_headings: tuple[str, ...]
    evidence_count: int
    first_position: int


def _artifact_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "skill_taxonomy.json"


def normalize_taxonomy_text(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("&", " and ")
    text = _WORD_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fallback_payload() -> dict[str, object]:
    skills: list[dict[str, object]] = []
    for canonical_name, aliases, category in _FALLBACK_SKILLS:
        normalized_forms: list[str] = []
        for item in [canonical_name, *aliases]:
            normalized = normalize_taxonomy_text(item)
            if normalized and normalized not in normalized_forms:
                normalized_forms.append(normalized)
        skills.append(
            {
                "id": normalize_taxonomy_text(canonical_name).replace(" ", "_"),
                "canonical_name": canonical_name,
                "category": category,
                "weight": _FALLBACK_WEIGHTS.get(category, 1.0),
                "aliases": aliases,
                "normalized_forms": normalized_forms,
                "cluster_id": f"cluster_{category}",
                "related_skill_ids": [],
                "match_tier": _default_match_tier(category=category, canonical_name=canonical_name),
                "intent_clusters": [f"cluster_{category}"],
                "strength_signals": [],
                "weak_signals": [],
            }
        )
    return {"schema_version": 1, "source_markdown": "compatibility_fallback", "categories": [], "skills": skills}


def _default_match_tier(*, category: str, canonical_name: str) -> str:
    category_key = normalize_taxonomy_text(category)
    canonical_key = normalize_taxonomy_text(canonical_name)
    if category_key.startswith("ai") or canonical_key in {
        "rag",
        "tool calling",
        "human in the loop",
        "guardrails",
        "embeddings",
        "observability",
        "responsible ai",
        "prompt engineering",
        "semantic search",
        "semantic retrieval",
        "agentic workflows",
        "ai evaluations",
        "groundedness",
    }:
        return "role_defining"
    if category_key in {
        "language",
        "backend",
        "framework",
        "database",
        "devops",
        "cloud",
        "security",
        "testing",
        "architecture",
        "data",
        "aws_service",
    }:
        return "foundation"
    if category_key in {"collaboration", "industry", "automation", "frontend", "design_tool", "build_tool", "messaging"}:
        return "supporting"
    return "generic"


def _default_intent_clusters(*, category: str, canonical_name: str, cluster_id: str) -> tuple[str, ...]:
    category_key = normalize_taxonomy_text(category)
    canonical_key = normalize_taxonomy_text(canonical_name)
    if canonical_key in {"rag", "semantic search", "semantic retrieval", "embeddings", "content chunking"}:
        return ("retrieval_rag", cluster_id)
    if canonical_key in {"tool calling", "agentic workflows", "guided intake", "runbook execution", "ticket summarization"}:
        return ("agentic_workflows", cluster_id)
    if canonical_key in {"human in the loop", "guardrails", "policy aware prompting", "fallback strategies"}:
        return ("safe_responsible_ai", cluster_id)
    if canonical_key in {"ai evaluations", "groundedness", "monitoring", "observability"}:
        return ("ai_operations", cluster_id)
    if canonical_key in {"secure sdlc", "least privilege", "data privacy", "responsible ai"}:
        return ("compliance_security", cluster_id)
    if category_key.startswith("ai"):
        return ("ai_runtime", cluster_id)
    if _default_match_tier(category=category, canonical_name=canonical_name) == "foundation":
        return ("production_engineering", cluster_id)
    return (cluster_id,)


def _default_strength_signals(canonical_name: str) -> tuple[str, ...]:
    canonical_key = normalize_taxonomy_text(canonical_name)
    if canonical_key in {"rag", "tool calling", "agentic workflows", "human in the loop", "prompt engineering", "embeddings"}:
        return ("hands on", "implemented", "built", "production", "design and deliver")
    return ()


def _default_weak_signals(canonical_name: str) -> tuple[str, ...]:
    canonical_key = normalize_taxonomy_text(canonical_name)
    if canonical_key in {"generative ai", "ai", "agentic workflows", "tool calling"}:
        return ("exposure", "concepts", "assisted", "familiarity", "awareness")
    return ()


def _coerce_entry(raw: object) -> SkillTaxonomyEntry | None:
    if not isinstance(raw, dict):
        return None
    canonical_name = str(raw.get("canonical_name", "")).strip()
    category = str(raw.get("category", "")).strip() or "uncategorized"
    entry_id = str(raw.get("id", "")).strip() or normalize_taxonomy_text(canonical_name).replace(" ", "_")
    if not canonical_name or not entry_id:
        return None
    aliases = tuple(str(item).strip() for item in raw.get("aliases", []) if str(item).strip())
    normalized_forms: list[str] = []
    for item in raw.get("normalized_forms", []) if isinstance(raw.get("normalized_forms", []), list) else []:
        normalized = normalize_taxonomy_text(item)
        if normalized and normalized not in normalized_forms:
            normalized_forms.append(normalized)
    for item in [canonical_name, *aliases]:
        normalized = normalize_taxonomy_text(item)
        if normalized and normalized not in normalized_forms:
            normalized_forms.append(normalized)
    if not normalized_forms:
        return None
    cluster_id = str(raw.get("cluster_id", f"cluster_{category}")).strip() or f"cluster_{category}"
    raw_intent_clusters = raw.get("intent_clusters", [])
    intent_clusters: list[str] = []
    if isinstance(raw_intent_clusters, list):
        for item in raw_intent_clusters:
            value = str(item).strip()
            if value and value not in intent_clusters:
                intent_clusters.append(value)
    if not intent_clusters:
        intent_clusters.extend(_default_intent_clusters(category=category, canonical_name=canonical_name, cluster_id=cluster_id))
    match_tier = str(raw.get("match_tier", "")).strip() or _default_match_tier(category=category, canonical_name=canonical_name)
    raw_strength_signals = raw.get("strength_signals", [])
    strength_signals = tuple(
        str(item).strip().lower()
        for item in raw_strength_signals
        if str(item).strip()
    ) or _default_strength_signals(canonical_name)
    raw_weak_signals = raw.get("weak_signals", [])
    weak_signals = tuple(
        str(item).strip().lower()
        for item in raw_weak_signals
        if str(item).strip()
    ) or _default_weak_signals(canonical_name)
    return SkillTaxonomyEntry(
        id=entry_id,
        canonical_name=canonical_name,
        category=category,
        weight=float(raw.get("weight", _FALLBACK_WEIGHTS.get(normalize_taxonomy_text(category), 1.0)) or 1.0),
        aliases=aliases,
        normalized_forms=tuple(normalized_forms),
        cluster_id=cluster_id,
        related_skill_ids=tuple(str(item).strip() for item in raw.get("related_skill_ids", []) if str(item).strip()),
        match_tier=match_tier,
        intent_clusters=tuple(intent_clusters),
        strength_signals=tuple(dict.fromkeys(strength_signals)),
        weak_signals=tuple(dict.fromkeys(weak_signals)),
    )


@lru_cache(maxsize=1)
def load_skill_taxonomy() -> SkillTaxonomy:
    payload: dict[str, object]
    try:
        payload = json.loads(_artifact_path().read_text(encoding="utf-8"))
    except Exception:
        payload = _fallback_payload()

    raw_skills = payload.get("skills", [])
    entries: list[SkillTaxonomyEntry] = []
    seen: set[str] = set()
    if isinstance(raw_skills, list):
        for raw in raw_skills:
            entry = _coerce_entry(raw)
            if entry is None or entry.id in seen:
                continue
            seen.add(entry.id)
            entries.append(entry)
    if not entries:
        fallback = _fallback_payload()
        for raw in fallback["skills"]:
            entry = _coerce_entry(raw)
            if entry is not None:
                entries.append(entry)

    # Source of truth is intentionally additive: the committed JSON is the curated seed,
    # while approved DB rows are the owner-specific live overlay.
    canonical_indexes = {normalize_taxonomy_text(entry.canonical_name): index for index, entry in enumerate(entries)}
    for raw in _custom_skill_payloads():
        entry = _coerce_entry(raw)
        if entry is None:
            continue
        canonical_key = normalize_taxonomy_text(entry.canonical_name)
        existing_index = canonical_indexes.get(canonical_key)
        if existing_index is not None:
            existing = entries[existing_index]
            aliases = tuple(dict.fromkeys((*existing.aliases, *entry.aliases)))
            normalized_forms = tuple(dict.fromkeys((*existing.normalized_forms, *entry.normalized_forms)))
            entries[existing_index] = replace(existing, aliases=aliases, normalized_forms=normalized_forms)
            continue
        if entry.id in seen:
            continue
        seen.add(entry.id)
        canonical_indexes[canonical_key] = len(entries)
        entries.append(entry)

    alias_owners: dict[str, list[SkillTaxonomyEntry]] = {}
    for entry in entries:
        for token in entry.normalized_forms:
            alias_owners.setdefault(token, []).append(entry)

    exact_lookup: dict[str, SkillTaxonomyEntry] = {}
    validation_warnings: list[str] = []
    ambiguous_aliases: set[str] = set()
    for token, owners in alias_owners.items():
        if token.isdigit():
            validation_warnings.append(f"numeric_only_alias:{token}")
            continue
        if len({owner.id for owner in owners}) > 1:
            ambiguous_aliases.add(token)
            validation_warnings.append(
                "ambiguous_alias:" + token + ":" + "|".join(sorted(owner.canonical_name for owner in owners))
            )
            continue
        exact_lookup[token] = owners[0]

    entries_for_search = tuple(
        sorted(
            entries,
            key=lambda item: (
                -max(len(token.split()) for token in item.normalized_forms),
                -max(len(token) for token in item.normalized_forms),
                item.canonical_name.lower(),
            ),
        )
    )
    return SkillTaxonomy(
        entries=tuple(entries),
        entries_for_search=entries_for_search,
        exact_lookup=exact_lookup,
        validation_warnings=tuple(validation_warnings),
        ambiguous_aliases=frozenset(ambiguous_aliases),
    )


def clear_skill_taxonomy_cache() -> None:
    _compiled_taxonomy_matcher.cache_clear()
    load_skill_taxonomy.cache_clear()


@lru_cache(maxsize=1)
def _compiled_taxonomy_matcher() -> AliasMatcher:
    return AliasMatcher(list(load_skill_taxonomy().exact_lookup))


def _custom_skill_payloads() -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    try:
        for seed in _load_custom_skill_seeds():
            canonical_name = str(seed.canonical_name).strip()
            if not canonical_name:
                continue
            category = str(seed.category or "custom").strip() or "custom"
            cluster_hint = str(seed.cluster_hint or "").strip() or f"custom_{normalize_taxonomy_text(category).replace(' ', '_') or 'skills'}"
            aliases = [str(alias).strip() for alias in seed.aliases if str(alias).strip()]
            entry_id = f"custom::{normalize_taxonomy_text(str(seed.owner_id or 'default-owner'))}::{normalize_taxonomy_text(canonical_name).replace(' ', '_')}"
            payloads.append(
                {
                    "id": entry_id,
                    "canonical_name": canonical_name,
                    "category": category,
                    "weight": float(seed.weight or _FALLBACK_WEIGHTS.get(normalize_taxonomy_text(category), 1.0)),
                    "aliases": aliases,
                    "normalized_forms": [canonical_name, *aliases],
                    "cluster_id": cluster_hint,
                    "related_skill_ids": [],
                    "match_tier": seed.match_tier or _default_match_tier(category=category, canonical_name=canonical_name),
                    "intent_clusters": [cluster_hint],
                    "strength_signals": [],
                    "weak_signals": [],
                }
            )
    except Exception:
        return []
    return payloads


def _load_custom_skill_seeds() -> tuple[CustomSkillSeed, ...]:
    try:
        import json as _json

        from app.db import SessionLocal
        from app.models import CustomSkillTaxonomyEntry

        with SessionLocal() as db:
            rows = (
                db.query(CustomSkillTaxonomyEntry)
                .filter(CustomSkillTaxonomyEntry.status == "approved")
                .order_by(CustomSkillTaxonomyEntry.owner_id.asc(), CustomSkillTaxonomyEntry.canonical_name.asc(), CustomSkillTaxonomyEntry.id.asc())
                .all()
            )
        seeds: list[CustomSkillSeed] = []
        for row in rows:
            raw_aliases = row.aliases_json or "[]"
            aliases: list[str] = []
            try:
                loaded = _json.loads(raw_aliases)
            except Exception:
                loaded = []
            if isinstance(loaded, list):
                aliases = [str(item).strip() for item in loaded if str(item).strip()]
            seeds.append(
                CustomSkillSeed(
                    canonical_name=row.canonical_name,
                    aliases=tuple(aliases),
                    category=row.category,
                    cluster_hint=row.cluster_hint,
                    description=row.description,
                    weight=float(row.weight or 1.0),
                    match_tier=row.match_tier or "supporting",
                    occurrence_count=int(row.occurrence_count or 0),
                    owner_id=row.owner_id,
                    status=row.status,
                )
            )
        return tuple(seeds)
    except Exception:
        return ()


def normalize_skill_token(token: str, *, preserve_unknown: bool = True) -> str | None:
    normalized = normalize_taxonomy_text(token)
    if not normalized or normalized == "none detected":
        return None
    entry = load_skill_taxonomy().exact_lookup.get(normalized)
    if entry:
        return entry.canonical_name
    cleaned = str(token or "").strip()
    if preserve_unknown and cleaned:
        return cleaned
    return None


def normalize_skills_text(skills_text: str | None, *, preserve_unknown: bool = True) -> str:
    ordered: list[str] = []
    seen: set[str] = set()
    for part in _SKILL_SPLIT_RE.split(str(skills_text or "")):
        normalized = normalize_skill_token(part, preserve_unknown=preserve_unknown)
        if not normalized:
            continue
        key = normalize_taxonomy_text(normalized)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(normalized)
    return ", ".join(ordered) if ordered else "none_detected"


def entries_from_skills_text(skills_text: str | None) -> list[SkillTaxonomyEntry]:
    ordered: list[SkillTaxonomyEntry] = []
    seen: set[str] = set()
    for part in _SKILL_SPLIT_RE.split(str(skills_text or "")):
        normalized = normalize_taxonomy_text(part)
        if not normalized or normalized == "none detected" or normalized in seen:
            continue
        seen.add(normalized)
        entry = load_skill_taxonomy().exact_lookup.get(normalized)
        if entry:
            ordered.append(entry)
    return ordered


def extract_taxonomy_skills(text: str | None) -> list[SkillTaxonomyEntry]:
    normalized_text = normalize_taxonomy_text(text)
    if not normalized_text:
        return []
    raw_text = str(text or "")
    taxonomy = load_skill_taxonomy()
    best_by_entry: dict[str, tuple[int, SkillTaxonomyEntry]] = {}
    for match in _compiled_taxonomy_matcher().find(normalized_text):
        entry = taxonomy.exact_lookup.get(match.alias)
        if entry is None or not _is_allowed_taxonomy_match(match.alias, entry, normalized_text, raw_text):
            continue
        current = best_by_entry.get(entry.id)
        if current is None or match.start < current[0]:
            best_by_entry[entry.id] = (match.start, entry)
    matches = [
        (position, -entry.weight, entry.canonical_name.lower(), entry)
        for position, entry in best_by_entry.values()
    ]
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [entry for *_ignored, entry in matches]


def extract_taxonomy_skill_matches(text: str | None) -> list[TaxonomySkillMatch]:
    normalized_text = normalize_taxonomy_text(text)
    if not normalized_text:
        return []
    raw_text = str(text or "")
    taxonomy = load_skill_taxonomy()
    best_by_entry: dict[str, tuple[int, str, SkillTaxonomyEntry]] = {}
    for match in _compiled_taxonomy_matcher().find(normalized_text):
        entry = taxonomy.exact_lookup.get(match.alias)
        if entry is None or not _is_allowed_taxonomy_match(match.alias, entry, normalized_text, raw_text):
            continue
        current = best_by_entry.get(entry.id)
        if current is None or match.start < current[0] or (match.start == current[0] and len(match.alias) > len(current[1])):
            best_by_entry[entry.id] = (match.start, match.alias, entry)
    matches = [
        (start, -entry.weight, entry.canonical_name.lower(), alias, entry)
        for start, alias, entry in best_by_entry.values()
    ]
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [
        TaxonomySkillMatch(
            entry=entry,
            matched_alias=alias,
            start=start,
            end=start + len(alias),
            normalized_text=normalized_text,
        )
        for start, _weight, _name, alias, entry in matches
    ]


def _is_allowed_taxonomy_match(token: str, entry: SkillTaxonomyEntry, normalized_text: str, raw_text: str) -> bool:
    guard = _MATCH_GUARDS.get(token)
    if not guard:
        return True
    if token == normalize_taxonomy_text(entry.canonical_name):
        return entry.canonical_name in raw_text
    if token in set(guard.get("allowed_phrases", set())):
        return True
    if guard.get("blocked_single_token", False) and len(token.split()) == 1:
        return False
    return True


def _entry_by_skill_id(skill_id: str) -> SkillTaxonomyEntry | None:
    for entry in load_skill_taxonomy().entries:
        if entry.id == skill_id:
            return entry
    return None


def _jd_skill_bucket_rank(bucket: str) -> int:
    order = {
        "mandatory": 0,
        "required": 1,
        "technical_skills": 2,
        "essential": 3,
        "domain": 4,
        "responsibilities": 5,
        "summary": 6,
        "ai_compliance": 7,
        "preferred": 8,
        "unknown": 9,
    }
    return order.get(bucket, 10)


def extract_jd_skill_evidence(sections: Iterable[JDSectionLike]) -> list[SkillEvidence]:
    evidence: list[SkillEvidence] = []
    for section_index, section in enumerate(sections):
        bucket = str(getattr(section, "bucket", "") or "unknown")
        if bucket in {"hard_filter", "footer"}:
            continue
        section_text = str(getattr(section, "text", "") or "").strip()
        if not section_text:
            continue
        section_heading = str(getattr(section, "heading", "") or "Body").strip() or "Body"
        section_weight = max(0.0, float(getattr(section, "weight", 0.0) or 0.0))
        if section_weight <= 0.0:
            continue
        matched_entries = extract_taxonomy_skills(section_text)
        for entry_index, entry in enumerate(matched_entries):
            evidence.append(
                SkillEvidence(
                    skill_id=entry.id,
                    canonical_name=entry.canonical_name,
                    bucket=bucket,
                    section_heading=section_heading,
                    section_weight=section_weight,
                    skill_weight=max(0.1, float(entry.weight)),
                    evidence_text=section_text,
                    first_position=(section_index * 1000) + entry_index,
                )
            )
    return evidence


def aggregate_jd_skill_evidence(evidence: Iterable[SkillEvidence]) -> list[AggregatedSkill]:
    grouped: dict[str, dict[str, object]] = {}
    for item in evidence:
        current = grouped.setdefault(
            item.skill_id,
            {
                "canonical_name": item.canonical_name,
                "total_weight": 0.0,
                "buckets": [],
                "headings": [],
                "count": 0,
                "first_position": item.first_position,
                "best_bucket_rank": _jd_skill_bucket_rank(item.bucket),
            },
        )
        current["total_weight"] = float(current["total_weight"]) + (item.section_weight * item.skill_weight)
        current["count"] = int(current["count"]) + 1
        current["first_position"] = min(int(current["first_position"]), item.first_position)
        current["best_bucket_rank"] = min(int(current["best_bucket_rank"]), _jd_skill_bucket_rank(item.bucket))
        if item.bucket not in current["buckets"]:
            current["buckets"].append(item.bucket)
        if item.section_heading not in current["headings"]:
            current["headings"].append(item.section_heading)

    aggregated = [
        AggregatedSkill(
            skill_id=skill_id,
            canonical_name=str(payload["canonical_name"]),
            total_weight=float(payload["total_weight"]),
            buckets=tuple(payload["buckets"]),
            section_headings=tuple(payload["headings"]),
            evidence_count=int(payload["count"]),
            first_position=int(payload["first_position"]),
        )
        for skill_id, payload in grouped.items()
    ]
    aggregated.sort(
        key=lambda item: (
            min(_jd_skill_bucket_rank(bucket) for bucket in item.buckets) if item.buckets else 10,
            -item.total_weight,
            item.first_position,
            item.canonical_name.lower(),
        )
    )
    return aggregated


def _aggregated_skill_is_foundation(skill: AggregatedSkill, entry: SkillTaxonomyEntry | None) -> bool:
    if entry is None:
        return False
    category = normalize_taxonomy_text(entry.category)
    return entry.match_tier == "foundation" or category in _FOUNDATION_CATEGORIES


def _aggregated_skill_is_aligned(skill: AggregatedSkill, entry: SkillTaxonomyEntry | None, role_family: str) -> bool:
    if entry is None or role_family == "general":
        return True
    config = _ROLE_FAMILY_CONFIG.get(role_family, _ROLE_FAMILY_CONFIG["general"])
    preferred_categories = {normalize_taxonomy_text(item) for item in config["preferred_categories"]}
    preferred_clusters = {normalize_taxonomy_text(item) for item in config["preferred_clusters"]}
    category = normalize_taxonomy_text(entry.category)
    clusters = {normalize_taxonomy_text(cluster) for cluster in entry.intent_clusters}
    if category in preferred_categories:
        return True
    if clusters & preferred_clusters:
        return True
    if role_family == "ai" and entry.match_tier == "role_defining":
        return True
    return False


def _aggregated_skill_is_weak_off_family(skill: AggregatedSkill, entry: SkillTaxonomyEntry | None, role_family: str) -> bool:
    if entry is None or role_family == "general":
        return False
    config = _ROLE_FAMILY_CONFIG.get(role_family, _ROLE_FAMILY_CONFIG["general"])
    suppressed_categories = {normalize_taxonomy_text(item) for item in config["suppress_categories"]}
    suppressed_skills = {normalize_taxonomy_text(item) for item in config["suppress_skills"]}
    category = normalize_taxonomy_text(entry.category)
    canonical = normalize_taxonomy_text(entry.canonical_name)
    if not (category in suppressed_categories or canonical in suppressed_skills):
        return False
    strong_bucket = any(bucket in _STRONG_EVIDENCE_BUCKETS for bucket in skill.buckets)
    return skill.evidence_count <= 1 and skill.total_weight < 0.85 and not strong_bucket


def filter_jd_skills_by_role_family(
    aggregated_skills: list[AggregatedSkill],
    *,
    role_family: str,
    role_text: str | None = None,
) -> list[AggregatedSkill]:
    if role_family == "general" or not aggregated_skills:
        return aggregated_skills

    filtered: list[AggregatedSkill] = []
    for skill in aggregated_skills:
        entry = _entry_by_skill_id(skill.skill_id)
        strong_bucket = any(bucket in _STRONG_EVIDENCE_BUCKETS for bucket in skill.buckets)
        aligned = _aggregated_skill_is_aligned(skill, entry, role_family)
        foundation = _aggregated_skill_is_foundation(skill, entry)
        weak_off_family = _aggregated_skill_is_weak_off_family(skill, entry, role_family)

        if strong_bucket or aligned or foundation:
            filtered.append(skill)
            continue
        if weak_off_family:
            continue
        filtered.append(skill)

    if not filtered or len(filtered) < min(3, len(aggregated_skills)):
        return aggregated_skills

    def sort_key(skill: AggregatedSkill) -> tuple[int, int, float, int]:
        entry = _entry_by_skill_id(skill.skill_id)
        aligned = _aggregated_skill_is_aligned(skill, entry, role_family)
        foundation = _aggregated_skill_is_foundation(skill, entry)
        strong_bucket = any(bucket in _STRONG_EVIDENCE_BUCKETS for bucket in skill.buckets)
        priority = 0 if strong_bucket else 1 if aligned else 2 if foundation else 3
        return (
            priority,
            min(_jd_skill_bucket_rank(bucket) for bucket in skill.buckets) if skill.buckets else 10,
            -skill.total_weight,
            skill.first_position,
        )

    return sorted(filtered, key=sort_key)


def extract_jd_skills_text(
    sections: Iterable[JDSectionLike],
    *,
    role_text: str | None = None,
    fallback_text: str | None = None,
) -> str:
    evidence = extract_jd_skill_evidence(sections)
    aggregated = aggregate_jd_skill_evidence(evidence)
    if aggregated:
        role_family = detect_role_family_from_entries(role_text, aggregated)
        filtered = filter_jd_skills_by_role_family(
            aggregated,
            role_family=role_family,
            role_text=role_text,
        )
        return ", ".join(item.canonical_name for item in filtered)
    if fallback_text:
        return extract_skills_text(fallback_text)
    return "none_detected"


def extract_skills_text(text: str | None) -> str:
    entries = extract_taxonomy_skills(text)
    if not entries:
        return "none_detected"
    return ", ".join(entry.canonical_name for entry in entries)


# Bumped whenever the family vocabulary or the thresholds below change, so the
# backfill can tell which stored classifications are stale. The `<system>:<version>`
# shape is deliberate: replacing this classifier with an external occupation
# standard becomes a value change (`onet:29.1`), not another migration.
ROLE_FAMILY_TAXONOMY_VERSION = "builtin:1"

# A title regex hit is the strongest signal this classifier has - the recruiter
# named the role. It is still a keyword match rather than an occupation lookup,
# which is why it is 0.9 and not 1.0.
_TITLE_REGEX_CONFIDENCE = 0.9
_AI_WEIGHT_THRESHOLD = 2.5
_FAMILY_WEIGHT_THRESHOLD = 2.0

# Ordered, and the order is load-bearing: "Full Stack Java Developer" matches both
# the fullstack and the backend rule, and fullstack has to win.
_ROLE_FAMILY_TITLE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_ROLE_FAMILY_JAVA_FULLSTACK_RE, "java_fullstack"),
    (_ROLE_FAMILY_FRONTEND_RE, "frontend"),
    (_ROLE_FAMILY_DEVOPS_RE, "devops_cloud"),
    (_ROLE_FAMILY_DATA_RE, "data"),
    (_ROLE_FAMILY_JAVA_BACKEND_RE, "java_backend"),
)


def _score_role_families(
    entries: Iterable[SkillTaxonomyEntry] | Iterable[AggregatedSkill],
) -> dict[str, float]:
    """Accumulate per-family weight from the skills alone, ignoring the title.

    Insertion order is load-bearing: `max()` breaks ties on it, so reordering these
    keys silently reclassifies every JD whose top two families are level.
    """
    scores = {
        "ai": 0.0,
        "java_fullstack": 0.0,
        "java_backend": 0.0,
        "frontend": 0.0,
        "devops_cloud": 0.0,
        "data": 0.0,
    }
    for raw_entry in entries:
        if isinstance(raw_entry, AggregatedSkill):
            entry = _entry_by_skill_id(raw_entry.skill_id)
            weight = max(0.1, raw_entry.total_weight)
        else:
            entry = raw_entry
            weight = max(0.1, float(entry.weight))
        if entry is None:
            continue
        category = normalize_taxonomy_text(entry.category)
        clusters = {normalize_taxonomy_text(cluster) for cluster in entry.intent_clusters}
        if entry.match_tier == "role_defining" or category.startswith("ai") or clusters & _ROLE_FAMILY_CONFIG["ai"]["preferred_clusters"]:
            scores["ai"] += weight
        if category in {"language", "backend", "framework", "architecture", "database", "devops", "cloud"}:
            scores["java_backend"] += weight
        if category in {"frontend", "language", "backend", "framework", "database", "devops", "cloud"}:
            scores["java_fullstack"] += weight
        if category == "frontend" or normalize_taxonomy_text(entry.canonical_name) in {"typescript", "react", "angular services"}:
            scores["frontend"] += weight
        if category in {"devops", "cloud", "observability", "security"}:
            scores["devops_cloud"] += weight
        if category in {"data", "database"}:
            scores["data"] += weight
    return scores


def _weight_confidence(family: str, scores: dict[str, float]) -> float:
    """How decisively the chosen family beat the runner-up.

    A landslide reads near 1.0 and a dead heat reads 0.5. The AI branch is the only
    one that can fall below 0.5, because AI wins on an absolute threshold rather
    than on being the maximum - so "AI, but java_backend scored higher" is recorded
    as the weak answer it is instead of being indistinguishable from a clean win.
    """
    best = scores.get(family, 0.0)
    if best <= 0.0:
        return 0.0
    runner_up = max((score for name, score in scores.items() if name != family), default=0.0)
    return round(max(0.0, min(1.0, 0.5 + 0.5 * ((best - runner_up) / best))), 4)


def classify_role_family(
    role_text: str | None, skills_text: str | None = None
) -> RoleFamilyClassification:
    """The role family ladder, carrying the evidence it decided on.

    Produces exactly the families `detect_role_family` has always produced - an AI
    signal outranks the other title rules, then the remaining title regexes in
    order, then the skill weights. Only the reported confidence and method are new,
    so this can be swapped in without reclassifying anything.

    Note the AI-by-weights branch sits *below* the title scan but is *checked*
    before it: the original ladder only consulted the skills for AI when no title
    rule matched at all, and that precedence is preserved deliberately.
    """
    normalized_role = normalize_taxonomy_text(role_text)
    scores = _score_role_families(entries_from_skills_text(skills_text))

    title_family: str | None = None
    if normalized_role:
        if _ROLE_FAMILY_AI_RE.search(normalized_role):
            return RoleFamilyClassification(
                "ai", _TITLE_REGEX_CONFIDENCE, "title_regex", ROLE_FAMILY_TAXONOMY_VERSION
            )
        for pattern, family in _ROLE_FAMILY_TITLE_RULES:
            if pattern.search(normalized_role):
                title_family = family
                break

    if title_family is None and scores["ai"] >= _AI_WEIGHT_THRESHOLD:
        return RoleFamilyClassification(
            "ai", _weight_confidence("ai", scores), "skill_weights", ROLE_FAMILY_TAXONOMY_VERSION
        )
    if title_family is not None:
        return RoleFamilyClassification(
            title_family, _TITLE_REGEX_CONFIDENCE, "title_regex", ROLE_FAMILY_TAXONOMY_VERSION
        )

    best_family = max(scores, key=scores.get)
    if scores[best_family] >= _FAMILY_WEIGHT_THRESHOLD:
        return RoleFamilyClassification(
            best_family,
            _weight_confidence(best_family, scores),
            "skill_weights",
            ROLE_FAMILY_TAXONOMY_VERSION,
        )
    return RoleFamilyClassification("general", 0.0, "unclassified", ROLE_FAMILY_TAXONOMY_VERSION)


def detect_role_family(role_text: str | None, skills_text: str | None = None) -> str:
    return classify_role_family(role_text, skills_text).family


def detect_role_family_from_entries(
    role_text: str | None,
    entries: Iterable[SkillTaxonomyEntry] | Iterable[AggregatedSkill],
) -> str:
    normalized_role = normalize_taxonomy_text(role_text)
    if normalized_role and _ROLE_FAMILY_AI_RE.search(normalized_role):
        return "ai"
    if normalized_role:
        for pattern, family in _ROLE_FAMILY_TITLE_RULES:
            if pattern.search(normalized_role):
                return family

    scores = _score_role_families(entries)
    if scores["ai"] >= _AI_WEIGHT_THRESHOLD:
        return "ai"
    best_family = max(scores, key=scores.get)
    if scores[best_family] >= _FAMILY_WEIGHT_THRESHOLD:
        return best_family
    return "general"


def role_family_fit_score(
    *,
    jd_role_family: str,
    resume_role_family: str,
    role_alignment_score: float,
    foundation_score: float,
    jd_priority_score: float,
) -> tuple[float, str]:
    adjusted = role_alignment_score
    if jd_role_family == resume_role_family:
        return max(0.0, min(adjusted, 1.0)), "direct_family_alignment"
    if jd_role_family == "general":
        return max(0.0, min(max(adjusted, 0.7), 1.0)), "general_family_fallback"
    if jd_role_family != "ai" and resume_role_family == "ai":
        if foundation_score >= 0.60 and jd_priority_score >= 0.50:
            return max(0.0, min(max(adjusted, 0.72), 1.0)), "ai_enabled_fullstack_override"
        if foundation_score < 0.55 and jd_priority_score < 0.50:
            return max(0.0, min(min(adjusted, 0.25), 1.0)), "generic_ai_guardrail"
    if foundation_score >= 0.65 and jd_priority_score >= 0.45:
        return max(0.0, min(max(adjusted, 0.68), 1.0)), "foundation_priority_override"
    return max(0.0, min(adjusted, 1.0)), "role_alignment_only"


def build_semantic_skill_summary(skills_text: str | None, *, role_text: str | None = None, limit: int = 12) -> str:
    entries = entries_from_skills_text(skills_text)
    if not entries:
        return ""
    ordered = sorted(
        entries,
        key=lambda entry: (
            0 if entry.match_tier == "role_defining" else 1 if entry.match_tier == "foundation" else 2,
            -entry.weight,
            entry.canonical_name.lower(),
        ),
    )
    selected = ordered[: max(1, limit)]
    parts = [entry.canonical_name for entry in selected]
    clusters = sorted(
        {
            cluster
            for entry in selected
            for cluster in entry.intent_clusters
            if cluster.startswith(("ai_", "retrieval_", "agentic_", "safe_", "compliance_", "production_"))
        }
    )
    role_family = detect_role_family(role_text, skills_text)
    prefix = f"RoleFamily:{role_family}"
    if role_text:
        prefix = f"{prefix}; Role:{role_text}"
    if clusters:
        return f"{prefix}; Clusters:{', '.join(clusters)}; Skills:{', '.join(parts)}"
    return f"{prefix}; Skills:{', '.join(parts)}"


def compute_intent_weighted_match(
    *,
    jd_role: str | None,
    jd_skills_text: str | None,
    resume_skills_text: str | None,
) -> IntentMatchBreakdown:
    jd_entries = entries_from_skills_text(jd_skills_text)
    resume_entries = entries_from_skills_text(resume_skills_text)
    resume_lookup = {normalize_taxonomy_text(entry.canonical_name): entry for entry in resume_entries}
    jd_role_family = detect_role_family(jd_role, jd_skills_text)
    resume_role_family = detect_role_family("", resume_skills_text)

    specialization_jd = [entry for entry in jd_entries if entry.match_tier == "role_defining"]
    foundation_jd = [entry for entry in jd_entries if entry.match_tier in {"foundation", "supporting"}]

    def _coverage(entries: list[SkillTaxonomyEntry]) -> tuple[float, list[str], list[str]]:
        if not entries:
            return 0.0, [], []
        matched_weight = 0.0
        total_weight = 0.0
        matched_names: list[str] = []
        missing_names: list[str] = []
        for entry in entries:
            entry_weight = max(0.1, float(entry.weight))
            total_weight += entry_weight
            if normalize_taxonomy_text(entry.canonical_name) in resume_lookup:
                matched_weight += entry_weight
                matched_names.append(entry.canonical_name)
            else:
                missing_names.append(entry.canonical_name)
        return (matched_weight / total_weight) if total_weight else 0.0, matched_names, missing_names

    specialization_score, matched_specialization, missing_specialization = _coverage(specialization_jd)
    foundation_score, _matched_foundation, _missing_foundation = _coverage(foundation_jd)

    jd_clusters = {
        cluster
        for entry in specialization_jd
        for cluster in entry.intent_clusters
        if cluster != entry.cluster_id
    }
    resume_clusters = {
        cluster
        for entry in resume_entries
        for cluster in entry.intent_clusters
        if cluster != entry.cluster_id
    }
    matched_clusters = sorted(jd_clusters & resume_clusters)
    if jd_clusters:
        cluster_score = len(matched_clusters) / len(jd_clusters)
        specialization_score = min(1.0, (specialization_score * 0.7) + (cluster_score * 0.3))

    role_alignment_score = 0.55
    if jd_role_family == "ai":
        role_alignment_score = 1.0 if resume_role_family == "ai" else 0.35
    elif jd_role_family == resume_role_family and jd_role_family != "general":
        role_alignment_score = 0.9
    elif jd_role_family == "general":
        role_alignment_score = 0.7

    normalized_resume_text = normalize_taxonomy_text(resume_skills_text)
    weak_signal_hits: list[str] = []
    for entry in resume_entries:
        for signal in entry.weak_signals:
            signal_normalized = normalize_taxonomy_text(signal)
            if signal_normalized and f" {signal_normalized} " in f" {normalized_resume_text} " and signal not in weak_signal_hits:
                weak_signal_hits.append(signal)
    if jd_role_family == "ai" and weak_signal_hits:
        role_alignment_score = max(0.0, role_alignment_score - min(0.12, 0.04 * len(weak_signal_hits)))
        specialization_score = max(0.0, specialization_score - min(0.08, 0.03 * len(weak_signal_hits)))

    if jd_role_family == "ai":
        score = (specialization_score * 0.6) + (foundation_score * 0.15) + (role_alignment_score * 0.25)
    else:
        score = (specialization_score * 0.25) + (foundation_score * 0.5) + (role_alignment_score * 0.25)

    return IntentMatchBreakdown(
        score=max(0.0, min(score, 1.0)),
        specialization_score=max(0.0, min(specialization_score, 1.0)),
        foundation_score=max(0.0, min(foundation_score, 1.0)),
        role_alignment_score=max(0.0, min(role_alignment_score, 1.0)),
        matched_specialization_skills=tuple(matched_specialization[:8]),
        missing_specialization_skills=tuple(missing_specialization[:8]),
        matched_clusters=tuple(matched_clusters[:6]),
        weak_signal_hits=tuple(weak_signal_hits[:6]),
        jd_role_family=jd_role_family,
        resume_role_family=resume_role_family,
    )


def score_taxonomy_skills(skills_text: str | None, *, per_weight_unit: float = 0.03, max_bonus: float = 0.21) -> float:
    total = 0.0
    seen: set[str] = set()
    for part in _SKILL_SPLIT_RE.split(str(skills_text or "")):
        normalized = normalize_taxonomy_text(part)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        entry = load_skill_taxonomy().exact_lookup.get(normalized)
        if entry is None:
            continue
        total += max(0.0, float(entry.weight)) * per_weight_unit
    return min(total, max_bonus)


def display_skill_label(skill_text: str | None) -> str:
    normalized = normalize_taxonomy_text(skill_text)
    if not normalized:
        return ""
    entry = load_skill_taxonomy().exact_lookup.get(normalized)
    if entry:
        return entry.canonical_name
    raw = str(skill_text or "").strip()
    if not raw:
        return ""
    return raw.title()
