from __future__ import annotations

from dataclasses import dataclass

from app.phase0 import build_skill_source_sections, slice_jd_sections
from app.skill_taxonomy import (
    aggregate_jd_skill_evidence,
    entries_from_skills_text,
    extract_jd_skill_evidence,
    extract_taxonomy_skills,
)

_PRIMARY_BUCKETS = {"mandatory", "required", "essential"}
_FALLBACK_BUCKETS = {"technical_skills"}


@dataclass(frozen=True)
class ColdCallSkillMatch:
    canonical: str
    display: str


def _allowed_requirement_skill_ids(requirement_text: str) -> list[str]:
    sections = slice_jd_sections(requirement_text or "")
    skill_sections = build_skill_source_sections(sections)
    aggregated = aggregate_jd_skill_evidence(extract_jd_skill_evidence(skill_sections))
    primary = [skill for skill in aggregated if set(skill.buckets) & _PRIMARY_BUCKETS]
    selected = primary or [skill for skill in aggregated if set(skill.buckets) & _FALLBACK_BUCKETS]
    seen: set[str] = set()
    ordered: list[str] = []
    for skill in selected:
        if skill.skill_id in seen:
            continue
        seen.add(skill.skill_id)
        ordered.append(skill.skill_id)
    return ordered


def _resume_entries(resume_skills_text: str, resume_text: str) -> list[object]:
    entries = entries_from_skills_text(resume_skills_text)
    if entries:
        return entries
    return extract_taxonomy_skills(resume_text)


def find_allowed_cold_call_skills(
    *,
    requirement_text: str,
    resume_skills_text: str,
    resume_text: str,
    max_skills: int = 2,
) -> list[ColdCallSkillMatch]:
    allowed_ids = _allowed_requirement_skill_ids(requirement_text)
    if not allowed_ids:
        return []

    resume_by_id = {entry.id: entry for entry in _resume_entries(resume_skills_text, resume_text)}
    matches: list[ColdCallSkillMatch] = []
    for skill_id in allowed_ids:
        entry = resume_by_id.get(skill_id)
        if not entry:
            continue
        matches.append(
            ColdCallSkillMatch(
                canonical=entry.canonical_name,
                display=entry.canonical_name,
            )
        )
        if len(matches) >= max_skills:
            break
    return matches


def find_known_skill_mentions(text: str) -> set[str]:
    return {entry.canonical_name for entry in extract_taxonomy_skills(text)}
