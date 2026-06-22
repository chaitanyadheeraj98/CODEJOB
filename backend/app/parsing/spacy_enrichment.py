from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any, Mapping

try:
    import spacy
    from spacy.language import Language
    from spacy.matcher import Matcher, PhraseMatcher
    from spacy.pipeline import EntityRuler
except ModuleNotFoundError:  # pragma: no cover - runtime-safe fallback
    spacy = None
    Language = Any  # type: ignore[assignment]
    Matcher = Any  # type: ignore[assignment]
    PhraseMatcher = Any  # type: ignore[assignment]
    EntityRuler = Any  # type: ignore[assignment]

from app.skill_taxonomy import extract_taxonomy_skills, normalize_skills_text

ROLE_LINE_RE = re.compile(
    r"\b(?:role|job title|title|position)\s*[:\-]\s*([^\n\r|]{3,160})",
    re.IGNORECASE,
)
COMPANY_RE = re.compile(
    r"\b(?:client|company|employer)\s*[:\-]\s*([A-Z][A-Za-z0-9&.,'() /\-]{1,120})",
    re.IGNORECASE,
)
AT_COMPANY_RE = re.compile(r"\bat\s+([A-Z][A-Za-z0-9&.,'() /\-]{2,120})")
LOCATION_LINE_RE = re.compile(
    r"\b(?:location|work location)\s*[:\-]\s*([^\n\r|]{2,160})",
    re.IGNORECASE,
)
CITY_STATE_RE = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*(?:[A-Z]{2}|[A-Z][a-z]+))\b"
)
EXPERIENCE_RE = re.compile(r"\b(\d{1,2})\s*\+?\s*years?\b", re.IGNORECASE)
WORK_MODE_RE = re.compile(r"\b(remote|hybrid|onsite|on site)\b", re.IGNORECASE)
VISA_RE = re.compile(r"\b(H1B|USC|GC|OPT|C2C|W2|1099|TN|EAD)\b", re.IGNORECASE)
ROLE_KEYWORD_RE = re.compile(
    r"\b("
    r"AI Engineer|ML Engineer|GenAI Engineer|LLM Engineer|Applied AI Engineer|Prompt Engineer|AI Developer|"
    r"Full Stack Developer|Full Stack Engineer|Java Developer|Java Backend Developer|Backend Developer|"
    r"Software Engineer|Technical Lead|API Integration Engineer|React Developer|Data Engineer|DevOps Engineer"
    r")\b",
    re.IGNORECASE,
)
BLOCKED_ROLE_SNIPPETS = ("<br", "<td", "<tr", "href=", "data-cfemail")


@dataclass(frozen=True)
class ParserEnrichment:
    role_candidates: list[str] = field(default_factory=list)
    company: str = ""
    primary_location: str = ""
    mentioned_locations: list[str] = field(default_factory=list)
    work_mode: str = ""
    visa_hints: list[str] = field(default_factory=list)
    experience_years_min: int | None = None
    skills_text: str = "none_detected"
    confidence: float = 0.0
    evidence: dict[str, list[str]] = field(default_factory=dict)


def enrichment_to_payload(enrichment: ParserEnrichment) -> dict[str, Any]:
    return asdict(enrichment)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered


def _clean_role_candidate(value: str) -> str:
    candidate = re.sub(r"\s+", " ", str(value or "")).strip(" ,:-|")
    if not candidate:
        return ""
    lowered = candidate.lower()
    if any(snippet in lowered for snippet in BLOCKED_ROLE_SNIPPETS):
        return ""
    if len(candidate) > 180:
        return ""
    return candidate


@lru_cache(maxsize=1)
def _build_runtime() -> tuple[Language | None, PhraseMatcher | None, Matcher | None]:
    if spacy is None:
        return None, None, None
    nlp = spacy.blank("en")
    ruler = nlp.add_pipe("entity_ruler")
    assert isinstance(ruler, EntityRuler)
    role_patterns = [
        {"label": "ROLE_HINT", "pattern": title}
        for title in (
            "AI Engineer",
            "ML Engineer",
            "GenAI Engineer",
            "LLM Engineer",
            "Applied AI Engineer",
            "Prompt Engineer",
            "AI Developer",
            "Full Stack Developer",
            "Full Stack Engineer",
            "Java Developer",
            "Java Backend Developer",
            "Backend Developer",
            "Software Engineer",
            "Technical Lead",
            "API Integration Engineer",
            "React Developer",
            "Data Engineer",
            "DevOps Engineer",
        )
    ]
    ruler.add_patterns(role_patterns)

    phrase_matcher = PhraseMatcher(nlp.vocab, attr="LOWER")
    skill_terms: list[str] = []
    for entry in extract_taxonomy_skills(", ".join(entry.canonical_name for entry in [])):  # pragma: no cover
        skill_terms.append(entry.canonical_name)
    from app.skill_taxonomy import load_skill_taxonomy

    for entry in load_skill_taxonomy().entries:
        skill_terms.append(entry.canonical_name)
        skill_terms.extend(entry.aliases[:2])
    skill_docs = [nlp.make_doc(term) for term in _dedupe(skill_terms) if term]
    if skill_docs:
        phrase_matcher.add("SKILL_HINT", skill_docs)

    matcher = Matcher(nlp.vocab)
    matcher.add(
        "EXPERIENCE_YEARS",
        [[{"LIKE_NUM": True}, {"TEXT": {"REGEX": r"\+?"}}, {"LOWER": {"IN": ["year", "years"]}}]],
    )
    return nlp, phrase_matcher, matcher


def _extract_role_candidates(subject: str, body: str, canonical_title: str | None) -> list[str]:
    candidates: list[str] = []
    if canonical_title:
        cleaned = _clean_role_candidate(canonical_title)
        if cleaned:
            candidates.append(cleaned)
    for match in ROLE_LINE_RE.findall(f"{subject}\n{body}"):
        cleaned = _clean_role_candidate(match)
        if cleaned:
            candidates.append(cleaned)
    for match in ROLE_KEYWORD_RE.findall(f"{subject}\n{body}"):
        cleaned = _clean_role_candidate(match)
        if cleaned:
            candidates.append(cleaned)
    return _dedupe(candidates)


def enrich_job_text(
    subject: str,
    body: str,
    *,
    source: str = "gmail",
    source_hints: Mapping[str, Any] | None = None,
) -> ParserEnrichment:
    canonical_title = str((source_hints or {}).get("canonical_title") or "").strip()
    location_hint = str((source_hints or {}).get("canonical_location") or "").strip()
    combined_text = "\n".join(part for part in [canonical_title or subject, body] if part).strip()
    if not combined_text:
        return ParserEnrichment()

    role_candidates = _extract_role_candidates(subject, body, canonical_title)
    company = ""
    if company_match := COMPANY_RE.search(combined_text):
        company = company_match.group(1).strip()
    elif at_match := AT_COMPANY_RE.search(combined_text):
        company = at_match.group(1).strip()

    primary_location = location_hint
    if not primary_location and (location_match := LOCATION_LINE_RE.search(combined_text)):
        primary_location = location_match.group(1).strip()

    mentioned_locations = _dedupe([primary_location, *CITY_STATE_RE.findall(combined_text)])
    work_mode_match = WORK_MODE_RE.search(combined_text)
    work_mode = work_mode_match.group(1).replace("on site", "onsite").title() if work_mode_match else ""
    visa_hints = _dedupe([match.upper() for match in VISA_RE.findall(combined_text)])
    years = [int(match) for match in EXPERIENCE_RE.findall(combined_text)]
    experience_years_min = min(years) if years else None

    skill_entries = extract_taxonomy_skills(combined_text)
    skills_text = normalize_skills_text(", ".join(entry.canonical_name for entry in skill_entries), preserve_unknown=False)

    evidence: dict[str, list[str]] = {
        "role_candidates": role_candidates[:5],
        "locations": mentioned_locations[:8],
        "visa_hints": visa_hints[:8],
        "skill_hits": [entry.canonical_name for entry in skill_entries[:20]],
    }
    if company:
        evidence["company"] = [company]
    if work_mode:
        evidence["work_mode"] = [work_mode]
    if canonical_title and source == "nvoids":
        evidence["canonical_title"] = [canonical_title]

    confidence = 0.1
    if role_candidates:
        confidence += 0.2
    if primary_location or mentioned_locations:
        confidence += 0.15
    if skills_text != "none_detected":
        confidence += min(0.35, len(skill_entries) * 0.02)
    if company:
        confidence += 0.1
    if work_mode:
        confidence += 0.05
    if visa_hints:
        confidence += 0.05

    nlp, phrase_matcher, matcher = _build_runtime()
    if nlp is not None:
        doc = nlp(combined_text)
        matched_skill_count = 0
        if phrase_matcher is not None:
            matched_skill_count = len(phrase_matcher(doc))
        if matcher is not None and experience_years_min is None:
            years_from_matcher: list[int] = []
            for _match_id, start, end in matcher(doc):
                snippet = doc[start:end].text
                if value_match := re.search(r"\d{1,2}", snippet):
                    years_from_matcher.append(int(value_match.group(0)))
            if years_from_matcher:
                experience_years_min = min(years_from_matcher)
        if matched_skill_count:
            confidence += min(0.1, matched_skill_count * 0.005)

    return ParserEnrichment(
        role_candidates=role_candidates,
        company=company,
        primary_location=primary_location,
        mentioned_locations=mentioned_locations,
        work_mode=work_mode,
        visa_hints=visa_hints,
        experience_years_min=experience_years_min,
        skills_text=skills_text,
        confidence=min(confidence, 0.95),
        evidence=evidence,
    )
