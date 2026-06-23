from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.skill_taxonomy import normalize_skill_token, normalize_taxonomy_text

_SKILL_SPLIT_RE = re.compile(r"[,;\n]+")


@dataclass(frozen=True)
class SkillAuditResult:
    skills_text: str = "none_detected"
    known: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    evidence: dict[str, list[str]] = field(default_factory=dict)


def _split_skill_tokens(skills_text: str | None) -> list[str]:
    return [part.strip() for part in _SKILL_SPLIT_RE.split(str(skills_text or "")) if part.strip()]


def audit_skills_text(skills_text: str | None) -> SkillAuditResult:
    ordered: list[str] = []
    known: list[str] = []
    unknown: list[str] = []
    evidence_known: list[str] = []
    evidence_unknown: list[str] = []
    seen_all: set[str] = set()
    seen_known: set[str] = set()
    seen_unknown: set[str] = set()

    for token in _split_skill_tokens(skills_text):
        normalized_known = normalize_skill_token(token, preserve_unknown=False)
        if normalized_known:
            key = normalize_taxonomy_text(normalized_known)
            if key and key not in seen_all:
                seen_all.add(key)
                ordered.append(normalized_known)
            if key and key not in seen_known:
                seen_known.add(key)
                known.append(normalized_known)
                evidence_known.append(f"{token} -> {normalized_known}")
            continue

        preserved = str(token or "").strip()
        key = normalize_taxonomy_text(preserved)
        if not preserved or not key:
            continue
        if key not in seen_all:
            seen_all.add(key)
            ordered.append(preserved)
        if key not in seen_unknown:
            seen_unknown.add(key)
            unknown.append(preserved)
            evidence_unknown.append(preserved)

    evidence: dict[str, list[str]] = {}
    if evidence_known:
        evidence["known"] = evidence_known
    if evidence_unknown:
        evidence["unknown"] = evidence_unknown

    return SkillAuditResult(
        skills_text=", ".join(ordered) if ordered else "none_detected",
        known=tuple(known),
        unknown=tuple(unknown),
        evidence=evidence,
    )


def skill_audit_result_to_payload(result: SkillAuditResult) -> dict[str, object]:
    return {
        "skills_text": result.skills_text,
        "known": list(result.known),
        "unknown": list(result.unknown),
        "evidence": result.evidence,
    }


def _coerce_skill_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token:
            continue
        key = normalize_taxonomy_text(token)
        if not key or key in seen:
            continue
        seen.add(key)
        items.append(token)
    return items


def _coerce_skill_evidence(value: object) -> dict[str, list[str]]:
    if not isinstance(value, Mapping):
        return {}
    payload: dict[str, list[str]] = {}
    for key, raw_items in value.items():
        label = str(key or "").strip()
        if not label:
            continue
        items = _coerce_skill_list(raw_items)
        if items:
            payload[label] = items
    return payload


def build_skills_json_payload(
    parser_details: Mapping[str, Any] | None,
    *,
    fallback_skills_text: str | None = None,
) -> dict[str, object]:
    candidate = parser_details.get("skills_audit") if isinstance(parser_details, Mapping) else None
    if isinstance(candidate, Mapping):
        skills_text = str(candidate.get("skills_text") or fallback_skills_text or "").strip() or "none_detected"
        known = _coerce_skill_list(candidate.get("known"))
        unknown = _coerce_skill_list(candidate.get("unknown"))
        evidence = _coerce_skill_evidence(candidate.get("evidence"))
        if skills_text != "none_detected" or known or unknown or evidence:
            return {
                "skills_text": skills_text,
                "known": known,
                "unknown": unknown,
                "evidence": evidence,
            }

    return skill_audit_result_to_payload(audit_skills_text(fallback_skills_text))
