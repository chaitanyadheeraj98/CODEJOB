from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_WORD_RE = re.compile(r"[^a-z0-9]+")
_SKILL_SPLIT_RE = re.compile(r"[,;\n]+")
_ROLE_FAMILY_AI_RE = re.compile(
    r"\b(ai|artificial intelligence|genai|llm|machine learning|ml|prompt)\b",
    re.IGNORECASE,
)
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
        weight=float(raw.get("weight", 1.0) or 1.0),
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

    exact_lookup: dict[str, SkillTaxonomyEntry] = {}
    for entry in entries:
        for token in entry.normalized_forms:
            exact_lookup.setdefault(token, entry)

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
    return SkillTaxonomy(entries=tuple(entries), entries_for_search=entries_for_search, exact_lookup=exact_lookup)


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
    haystack = f" {normalized_text} "
    matches: list[tuple[int, float, str, SkillTaxonomyEntry]] = []
    seen: set[str] = set()
    for entry in load_skill_taxonomy().entries_for_search:
        first_match: int | None = None
        for token in entry.normalized_forms:
            position = haystack.find(f" {token} ")
            if position >= 0 and (first_match is None or position < first_match):
                first_match = position
        if first_match is None or entry.id in seen:
            continue
        seen.add(entry.id)
        matches.append((first_match, -entry.weight, entry.canonical_name.lower(), entry))
    matches.sort(key=lambda item: (item[0], item[1], item[2]))
    return [entry for *_ignored, entry in matches]


def extract_skills_text(text: str | None) -> str:
    entries = extract_taxonomy_skills(text)
    if not entries:
        return "none_detected"
    return ", ".join(entry.canonical_name for entry in entries)


def detect_role_family(role_text: str | None, skills_text: str | None = None) -> str:
    normalized_role = normalize_taxonomy_text(role_text)
    if normalized_role and _ROLE_FAMILY_AI_RE.search(normalized_role):
        return "ai"
    entries = entries_from_skills_text(skills_text)
    ai_like = sum(1 for entry in entries if entry.match_tier == "role_defining")
    if ai_like >= 3:
        return "ai"
    if normalized_role and any(token in normalized_role for token in ("java", "backend", "frontend", "full stack", "software engineer", "developer")):
        return "software"
    return "general"


def group_entries_by_cluster(entries: list[SkillTaxonomyEntry]) -> dict[str, list[SkillTaxonomyEntry]]:
    grouped: dict[str, list[SkillTaxonomyEntry]] = {}
    for entry in entries:
        for cluster in entry.intent_clusters:
            grouped.setdefault(cluster, []).append(entry)
    return grouped


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
