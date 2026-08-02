from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from app.skill_taxonomy import (
    TAXONOMY_PLACEHOLDER_KEYS,
    extract_taxonomy_skill_matches,
    normalize_skill_token,
    normalize_taxonomy_text,
)

_SKILL_SPLIT_RE = re.compile(r"[\n,;/|]+|\.(?=\s+[A-Z])\s*")
_SUSPICIOUS_SKILL_BLOB_MIN_WORDS = 7
_SUSPICIOUS_SKILL_BLOB_MIN_CHARS = 80
_LEFTOVER_FRAGMENT_WORDS = frozenset({"is", "a", "with", "for", "on", "plus", "and", "or", "including", "the"})


@dataclass(frozen=True)
class SkillAuditResult:
    skills_text: str = "none_detected"
    known: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    evidence: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillCandidateAnalysis:
    recovered_skills: tuple[str, ...] = ()
    leftover_text: str = ""
    suspicious: bool = False


def split_skill_tokens(skills_text: str | None) -> list[str]:
    text = re.sub(r"\bn\s*/\s*a\b", "", str(skills_text or ""), flags=re.IGNORECASE)
    return [part.strip() for part in _SKILL_SPLIT_RE.split(text) if part.strip()]


def _has_unmatched_brackets(value: str) -> bool:
    return any(value.count(opening) != value.count(closing) for opening, closing in (("(", ")"), ("[", "]")))


def _is_long_fragment(value: str) -> bool:
    words = [part for part in re.split(r"\s+", value) if part]
    return len(words) >= _SUSPICIOUS_SKILL_BLOB_MIN_WORDS or len(value) >= _SUSPICIOUS_SKILL_BLOB_MIN_CHARS


def _remove_match_spans(normalized_text: str, spans: list[tuple[int, int]]) -> str:
    masked = list(normalized_text)
    for start, end in spans:
        for index in range(max(0, start), min(len(masked), end)):
            masked[index] = " "
    return " ".join("".join(masked).split())


def analyze_skill_candidate(value: str | None) -> SkillCandidateAnalysis:
    text = str(value or "").strip()
    if not text:
        return SkillCandidateAnalysis()
    normalized = normalize_taxonomy_text(text)
    if not normalized or normalized in TAXONOMY_PLACEHOLDER_KEYS or all(word.isdigit() for word in normalized.split()):
        return SkillCandidateAnalysis(suspicious=True)

    exact = normalize_skill_token(text, preserve_unknown=False)
    if exact:
        return SkillCandidateAnalysis(recovered_skills=(exact,))

    matches = extract_taxonomy_skill_matches(text)
    recovered: list[str] = []
    seen: set[str] = set()
    spans: list[tuple[int, int]] = []
    for match in matches:
        key = normalize_taxonomy_text(match.entry.canonical_name)
        spans.append((match.start, match.end))
        if key and key not in seen:
            seen.add(key)
            recovered.append(match.entry.canonical_name)

    leftover = _remove_match_spans(normalized, spans) if spans else normalized
    leftover_words = set(leftover.split())
    suspicious = _has_unmatched_brackets(text) or _is_long_fragment(leftover or text)
    if recovered and leftover_words & _LEFTOVER_FRAGMENT_WORDS:
        suspicious = True
    return SkillCandidateAnalysis(
        recovered_skills=tuple(recovered),
        leftover_text=leftover,
        suspicious=suspicious,
    )


def is_suspicious_skill_blob(value: str | None) -> bool:
    return analyze_skill_candidate(value).suspicious


def recover_known_skills_from_blob(value: str | None) -> tuple[str, ...]:
    return analyze_skill_candidate(value).recovered_skills


def is_safe_for_bulk_skill_approval(value: str | None) -> bool:
    text = str(value or "").strip()
    tokens = split_skill_tokens(text)
    if len(tokens) != 1 or tokens[0] != text:
        return False
    analysis = analyze_skill_candidate(text)
    if analysis.suspicious or analysis.recovered_skills:
        return False
    audited = audit_skills_text(text)
    return audited.skills_text == text and audited.unknown == (text,)


def custom_skill_requires_review(value: str | None) -> bool:
    text = str(value or "").strip()
    normalized = normalize_taxonomy_text(text)
    if not normalized or normalized in TAXONOMY_PLACEHOLDER_KEYS or not any(char.isalpha() for char in text):
        return True
    if len(split_skill_tokens(text)) != 1 or _has_unmatched_brackets(text) or _is_long_fragment(text):
        return True
    return bool(text[:1].islower() and set(normalized.split()) & _LEFTOVER_FRAGMENT_WORDS)


def audit_skills_text(skills_text: str | None) -> SkillAuditResult:
    ordered: list[str] = []
    known: list[str] = []
    unknown: list[str] = []
    evidence_known: list[str] = []
    evidence_unknown: list[str] = []
    seen_all: set[str] = set()
    seen_known: set[str] = set()
    seen_unknown: set[str] = set()

    for token in split_skill_tokens(skills_text):
        if normalize_taxonomy_text(token) in TAXONOMY_PLACEHOLDER_KEYS:
            continue
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

        analysis = analyze_skill_candidate(token)
        recovered_known = analysis.recovered_skills
        if recovered_known:
            for recovered in recovered_known:
                key = normalize_taxonomy_text(recovered)
                if key and key not in seen_all:
                    seen_all.add(key)
                    ordered.append(recovered)
                if key and key not in seen_known:
                    seen_known.add(key)
                    known.append(recovered)
                    evidence_known.append(f"{token} -> {recovered} (recovered)")
            continue

        if analysis.suspicious:
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


def skill_audit_result_to_payload(result: SkillAuditResult, *, unknown_source: str = "legacy") -> dict[str, object]:
    return {
        "skills_text": result.skills_text,
        "known": list(result.known),
        "unknown": list(result.unknown),
        "evidence": result.evidence,
        "unknown_source": unknown_source if unknown_source in {"ai", "base", "legacy"} else "legacy",
    }


def build_skills_json_payload(
    parser_details: Mapping[str, Any] | None,
    *,
    fallback_skills_text: str | None = None,
) -> dict[str, object]:
    candidate = parser_details.get("skills_audit") if isinstance(parser_details, Mapping) else None
    source = "legacy"
    if isinstance(candidate, Mapping):
        source = str(candidate.get("unknown_source") or "legacy")
    if source == "legacy" and isinstance(parser_details, Mapping):
        mode = str(parser_details.get("parser_mode") or "")
        source = "ai" if mode == "ai_primary" else "base" if mode in {"base_only", "ai_fallback"} else "legacy"
    skills_text = ""
    if isinstance(candidate, Mapping):
        skills_text = str(candidate.get("skills_text") or "").strip()
        if not skills_text:
            stored_values: list[str] = []
            for key in ("known", "unknown"):
                raw_values = candidate.get(key)
                if isinstance(raw_values, list):
                    stored_values.extend(str(item).strip() for item in raw_values if str(item).strip())
            skills_text = ", ".join(stored_values)
    if not skills_text:
        skills_text = str(fallback_skills_text or "").strip()
    return skill_audit_result_to_payload(audit_skills_text(skills_text), unknown_source=source)
