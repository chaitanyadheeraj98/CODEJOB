from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.skill_taxonomy import TaxonomySkillMatch, extract_taxonomy_skill_matches, normalize_taxonomy_text

REQUIREMENTS_SCHEMA_VERSION = 1

_MANDATORY_BUCKETS = {"mandatory", "required", "technical_skills", "essential"}
_PREFERRED_BUCKETS = {"preferred"}
_PREFERRED_PATTERNS = (
    re.compile(r"\b(preferred|nice to have|good to have|plus|bonus|familiarity with)\b", re.I),
)
_MANDATORY_PATTERNS = (
    re.compile(r"\b(must have|required|required skills|required qualifications)\b", re.I),
    re.compile(r"\b(strong proficiency in|proficiency in|hands-on experience|experience with)\b", re.I),
)
_SPLIT_RE = re.compile(r"(?:\r?\n\s*[-*•]?\s*|\r?\n+|;\s+)")
_LOCAL_ONLY_RE = re.compile(r"\b(?:locals? only|local candidates? only|need locals?)\b", re.I)
_WORK_MODE_RE = re.compile(r"\b(remote|hybrid|onsite|on site)\b", re.I)
_EXPERIENCE_RE = re.compile(r"\b(\d{1,2})\s*\+?\s*years?\b", re.I)
_CITY_STATE_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*,\s*(?:[A-Z]{2}|[A-Z][a-z]+))\b")
_DOMAIN_RE = re.compile(r"\b(?:preferred|plus|good to have).{0,30}\b(?:with|at|from|in)\s+([A-Z][A-Za-z0-9&.\- ]+)\b")
_PROTECTED_SLASH_TERMS = {"ci cd", "tcp ip", "ui ux", "oauth oidc"}


@dataclass(frozen=True)
class SkillRequirement:
    skill_id: str
    canonical_name: str
    matched_alias: str | None
    evidence_text: str
    versions: tuple[str, ...] = ()
    qualifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequirementGroup:
    group_id: str
    level: str
    mode: str
    skills: tuple[SkillRequirement, ...]
    evidence_text: str
    section_heading: str
    section_bucket: str


@dataclass(frozen=True)
class ParsedJDRequirements:
    required_groups: tuple[RequirementGroup, ...] = ()
    preferred_groups: tuple[RequirementGroup, ...] = ()
    informational_groups: tuple[RequirementGroup, ...] = ()
    experience_years_min: int | None = None
    local_required: bool = False
    work_mode: str | None = None
    locations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    preferred_domains: tuple[str, ...] = ()


class JDSectionLike(Protocol):
    heading: str
    bucket: str
    text: str


def _dedupe_strings(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return tuple(ordered)


def _split_requirement_units(text: str) -> list[str]:
    units: list[str] = []
    for part in _SPLIT_RE.split(str(text or "")):
        cleaned = re.sub(r"\s+", " ", part).strip(" -*•\t")
        if cleaned:
            units.append(cleaned)
    return units


def _classify_level(sentence: str, bucket: str) -> str:
    if bucket in _PREFERRED_BUCKETS:
        return "preferred"
    if any(pattern.search(sentence) for pattern in _PREFERRED_PATTERNS):
        return "preferred"
    if bucket in _MANDATORY_BUCKETS:
        return "mandatory"
    if any(pattern.search(sentence) for pattern in _MANDATORY_PATTERNS):
        return "mandatory"
    return "informational"


def _extract_versions(sentence: str, match: TaxonomySkillMatch) -> tuple[str, ...]:
    remainder = match.normalized_text[match.end :]
    version_match = re.match(r"\s*(?:version\s*)?(\d+(?:\s*(?:/|or|,)\s*\d+)*)", remainder)
    if not version_match:
        return ()
    return tuple(part for part in re.split(r"\s*(?:/|or|,)\s*", version_match.group(1)) if part)


def _build_skill_requirement(sentence: str, match: TaxonomySkillMatch) -> SkillRequirement:
    qualifiers: list[str] = []
    normalized_sentence = normalize_taxonomy_text(sentence)
    if "concepts" in normalized_sentence:
        qualifiers.append("concepts")
    if "familiarity" in normalized_sentence:
        qualifiers.append("familiarity")
    return SkillRequirement(
        skill_id=match.entry.id,
        canonical_name=match.entry.canonical_name,
        matched_alias=match.matched_alias,
        evidence_text=sentence,
        versions=_extract_versions(sentence, match),
        qualifiers=tuple(dict.fromkeys(qualifiers)),
    )


def _connectors(matches: Sequence[TaxonomySkillMatch]) -> list[str]:
    connectors: list[str] = []
    for idx in range(len(matches) - 1):
        current = matches[idx]
        nxt = matches[idx + 1]
        connectors.append(current.normalized_text[current.end : nxt.start].strip())
    return connectors


def _build_groups_for_sentence(
    sentence: str,
    *,
    heading: str,
    bucket: str,
    level: str,
    group_prefix: str,
) -> list[RequirementGroup]:
    matches = extract_taxonomy_skill_matches(sentence)
    if not matches:
        return []
    connectors = _connectors(matches)
    any_like = {"or", "and or", "and/or"}
    has_global_any = bool(connectors) and any(conn in any_like or conn.endswith(" or") or conn.startswith("or ") for conn in connectors)
    slash_runs = ["/" in conn for conn in connectors]
    groups: list[RequirementGroup] = []

    if has_global_any and all(("/" in conn) or (not conn) or ("or" in conn) or ("," in conn) for conn in connectors):
        skills = tuple(_build_skill_requirement(sentence, match) for match in matches)
        groups.append(
            RequirementGroup(
                group_id=f"{group_prefix}:0",
                level=level,
                mode="any",
                skills=skills,
                evidence_text=sentence,
                section_heading=heading,
                section_bucket=bucket,
            )
        )
        return groups

    if any(slash_runs):
        consumed: set[int] = set()
        group_idx = 0
        for idx, match in enumerate(matches):
            if idx in consumed:
                continue
            run = [match]
            run_any = False
            lookahead = idx
            while lookahead < len(connectors) and "/" in connectors[lookahead]:
                next_match = matches[lookahead + 1]
                pair_phrase = normalize_taxonomy_text(f"{matches[lookahead].entry.canonical_name} {next_match.entry.canonical_name}")
                if pair_phrase in _PROTECTED_SLASH_TERMS:
                    break
                run_any = True
                run.append(next_match)
                consumed.add(lookahead + 1)
                lookahead += 1
            groups.append(
                RequirementGroup(
                    group_id=f"{group_prefix}:{group_idx}",
                    level=level,
                    mode="any" if run_any else "all",
                    skills=tuple(_build_skill_requirement(sentence, item) for item in run),
                    evidence_text=sentence,
                    section_heading=heading,
                    section_bucket=bucket,
                )
            )
            group_idx += 1
        return groups

    groups.append(
        RequirementGroup(
            group_id=f"{group_prefix}:0",
            level=level,
            mode="all",
            skills=tuple(_build_skill_requirement(sentence, match) for match in matches),
            evidence_text=sentence,
            section_heading=heading,
            section_bucket=bucket,
        )
    )
    return groups


def parse_structured_jd_requirements(
    sections: Sequence[JDSectionLike],
    *,
    full_text: str,
    source_hints: Mapping[str, Any] | None = None,
) -> ParsedJDRequirements:
    required_groups: list[RequirementGroup] = []
    preferred_groups: list[RequirementGroup] = []
    informational_groups: list[RequirementGroup] = []
    warnings: list[str] = []

    for section_index, section in enumerate(sections):
        if section.bucket in {"footer", "hard_filter"}:
            continue
        for unit_index, sentence in enumerate(_split_requirement_units(section.text)):
            level = _classify_level(sentence, section.bucket)
            groups = _build_groups_for_sentence(
                sentence,
                heading=section.heading,
                bucket=section.bucket,
                level=level,
                group_prefix=f"{section_index}-{unit_index}",
            )
            if not groups:
                continue
            if level == "mandatory":
                required_groups.extend(groups)
            elif level == "preferred":
                preferred_groups.extend(groups)
            else:
                informational_groups.extend(groups)

    years = [int(value) for value in _EXPERIENCE_RE.findall(full_text or "")]
    hinted_location = str((source_hints or {}).get("canonical_location") or "").strip()
    locations = _dedupe_strings([hinted_location, *_CITY_STATE_RE.findall(full_text or "")])
    work_mode_match = _WORK_MODE_RE.search(full_text or "")
    preferred_domains = _dedupe_strings(match.strip(" ,.") for match in _DOMAIN_RE.findall(full_text or ""))

    return ParsedJDRequirements(
        required_groups=tuple(required_groups),
        preferred_groups=tuple(preferred_groups),
        informational_groups=tuple(informational_groups),
        experience_years_min=max(years) if years else None,
        local_required=bool(_LOCAL_ONLY_RE.search(full_text or "")),
        work_mode=work_mode_match.group(1).replace("on site", "onsite").title() if work_mode_match else None,
        locations=locations,
        warnings=tuple(warnings),
        preferred_domains=preferred_domains,
    )


def structured_requirements_from_ai_payload(
    ai_payload: Mapping[str, Any] | None,
    *,
    fallback: ParsedJDRequirements,
) -> ParsedJDRequirements:
    payload = dict(ai_payload or {})
    required_groups: list[RequirementGroup] = []
    preferred_groups: list[RequirementGroup] = []
    informational_groups: list[RequirementGroup] = []

    def build_groups(values: Iterable[str], *, level: str, prefix: str) -> list[RequirementGroup]:
        groups: list[RequirementGroup] = []
        for index, value in enumerate(values):
            sentence = str(value or "").strip()
            groups.extend(
                _build_groups_for_sentence(
                    sentence,
                    heading="AI Extractor",
                    bucket=level,
                    level=level,
                    group_prefix=f"{prefix}-{index}",
                )
            )
        return groups

    required_values = payload.get("must_have_skills") or []
    preferred_values = payload.get("nice_to_have_skills") or []
    info_values = payload.get("skills") or []
    if isinstance(required_values, str):
        required_values = [item.strip() for item in required_values.split(",") if item.strip()]
    if isinstance(preferred_values, str):
        preferred_values = [item.strip() for item in preferred_values.split(",") if item.strip()]
    if isinstance(info_values, str):
        info_values = [item.strip() for item in info_values.split(",") if item.strip()]

    required_groups.extend(build_groups(required_values, level="mandatory", prefix="ai-required"))
    preferred_groups.extend(build_groups(preferred_values, level="preferred", prefix="ai-preferred"))
    informational_groups.extend(build_groups(info_values, level="informational", prefix="ai-informational"))

    if not required_groups:
        required_groups.extend(fallback.required_groups)
    if not preferred_groups:
        preferred_groups.extend(fallback.preferred_groups)
    if not informational_groups:
        informational_groups.extend(fallback.informational_groups)

    return ParsedJDRequirements(
        required_groups=tuple(required_groups),
        preferred_groups=tuple(preferred_groups),
        informational_groups=tuple(informational_groups),
        experience_years_min=int(payload.get("experience_years_min")) if payload.get("experience_years_min") not in (None, "") else fallback.experience_years_min,
        local_required=fallback.local_required,
        work_mode=str(payload.get("work_mode") or fallback.work_mode or "").strip() or fallback.work_mode,
        locations=_dedupe_strings([str(payload.get("primary_location") or "").strip(), *fallback.locations]),
        warnings=fallback.warnings,
        preferred_domains=fallback.preferred_domains,
    )


def flatten_requirement_skills(requirements: ParsedJDRequirements) -> str:
    seen: set[str] = set()
    ordered: list[str] = []
    for group in (*requirements.required_groups, *requirements.preferred_groups, *requirements.informational_groups):
        for skill in group.skills:
            key = normalize_taxonomy_text(skill.canonical_name)
            if not key or key in seen:
                continue
            seen.add(key)
            ordered.append(skill.canonical_name)
    return ", ".join(ordered) if ordered else "none_detected"


def requirements_to_payload(requirements: ParsedJDRequirements) -> dict[str, Any]:
    def skill_payload(skill: SkillRequirement) -> dict[str, Any]:
        return {
            "skill_id": skill.skill_id,
            "canonical_name": skill.canonical_name,
            "matched_alias": skill.matched_alias,
            "evidence_text": skill.evidence_text,
            "versions": list(skill.versions),
            "qualifiers": list(skill.qualifiers),
        }

    def group_payload(group: RequirementGroup) -> dict[str, Any]:
        return {
            "group_id": group.group_id,
            "level": group.level,
            "mode": group.mode,
            "skills": [skill_payload(skill) for skill in group.skills],
            "evidence_text": group.evidence_text,
            "section_heading": group.section_heading,
            "section_bucket": group.section_bucket,
        }

    return {
        "schema_version": REQUIREMENTS_SCHEMA_VERSION,
        "required_groups": [group_payload(group) for group in requirements.required_groups],
        "preferred_groups": [group_payload(group) for group in requirements.preferred_groups],
        "informational_groups": [group_payload(group) for group in requirements.informational_groups],
        "experience_years_min": requirements.experience_years_min,
        "local_required": requirements.local_required,
        "work_mode": requirements.work_mode,
        "locations": list(requirements.locations),
        "warnings": list(requirements.warnings),
        "preferred_domains": list(requirements.preferred_domains),
    }


def requirements_from_payload(payload: Mapping[str, Any] | None) -> ParsedJDRequirements:
    raw = dict(payload or {})

    def load_skill(item: Mapping[str, Any]) -> SkillRequirement:
        return SkillRequirement(
            skill_id=str(item.get("skill_id") or ""),
            canonical_name=str(item.get("canonical_name") or ""),
            matched_alias=str(item.get("matched_alias") or "").strip() or None,
            evidence_text=str(item.get("evidence_text") or ""),
            versions=tuple(str(value) for value in item.get("versions", []) if str(value).strip()),
            qualifiers=tuple(str(value) for value in item.get("qualifiers", []) if str(value).strip()),
        )

    def load_group(item: Mapping[str, Any]) -> RequirementGroup:
        return RequirementGroup(
            group_id=str(item.get("group_id") or ""),
            level=str(item.get("level") or "informational"),
            mode=str(item.get("mode") or "all"),
            skills=tuple(load_skill(skill) for skill in item.get("skills", []) if isinstance(skill, Mapping)),
            evidence_text=str(item.get("evidence_text") or ""),
            section_heading=str(item.get("section_heading") or "Body"),
            section_bucket=str(item.get("section_bucket") or "unknown"),
        )

    return ParsedJDRequirements(
        required_groups=tuple(load_group(group) for group in raw.get("required_groups", []) if isinstance(group, Mapping)),
        preferred_groups=tuple(load_group(group) for group in raw.get("preferred_groups", []) if isinstance(group, Mapping)),
        informational_groups=tuple(load_group(group) for group in raw.get("informational_groups", []) if isinstance(group, Mapping)),
        experience_years_min=raw.get("experience_years_min") if isinstance(raw.get("experience_years_min"), int) else None,
        local_required=bool(raw.get("local_required", False)),
        work_mode=str(raw.get("work_mode") or "").strip() or None,
        locations=tuple(str(value) for value in raw.get("locations", []) if str(value).strip()),
        warnings=tuple(str(value) for value in raw.get("warnings", []) if str(value).strip()),
        preferred_domains=tuple(str(value) for value in raw.get("preferred_domains", []) if str(value).strip()),
    )
