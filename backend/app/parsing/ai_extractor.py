from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.ai.deepseek_client import deepseek_json_completion
from app.skill_taxonomy import normalize_skill_token


@dataclass(frozen=True)
class AIExtractorResult:
    role_candidates: tuple[str, ...] = ()
    company: str = ""
    primary_location: str = ""
    mentioned_locations: tuple[str, ...] = ()
    work_mode: str = ""
    salary_text: str = ""
    visa_hints: tuple[str, ...] = ()
    experience_years_min: int | None = None
    skills_text: str = ""
    f2f_mentioned: bool = False
    asks_contact_fields: bool = False
    is_texas_role: bool = False
    # Legacy compatibility fields; Phase 3 moves taxonomy audit out of the extractor.
    skills_approved: tuple[str, ...] = ()
    skills_unknown: tuple[str, ...] = ()
    confidence: float = 0.0
    evidence: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _as_string_list(value: object) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            text = _clean_text(item)
            if text:
                items.append(text)
        return items
    return []


def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return tuple(ordered)


def _normalize_confidence(value: object) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(confidence, 1.0))


def _normalize_years(value: object) -> int | None:
    if value in (None, "", False):
        return None
    try:
        years = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None
    return max(0, years)


def _normalize_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = _clean_text(value).lower()
    if not text:
        return False
    return text in {"1", "true", "yes", "y", "mentioned", "present"}


def _normalize_evidence(value: object) -> dict[str, list[str]]:
    if isinstance(value, dict):
        normalized: dict[str, list[str]] = {}
        for key, raw in value.items():
            label = _clean_text(key)
            if not label:
                continue
            values = _as_string_list(raw)
            if values:
                normalized[label] = values
        return normalized
    values = _as_string_list(value)
    return {"raw": values} if values else {}


_SKILL_SPLIT_RE = re.compile(r"[\n,;/|]+")


def _split_free_skill_text(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in _SKILL_SPLIT_RE.split(value) if item.strip()]


def _dedupe_skill_values(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(cleaned)
    return tuple(ordered)


def _collect_free_skill_values(payload: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("skills_text", "skills", "must_have_skills", "nice_to_have_skills", "skills_approved", "skills_unknown"):
        raw = payload.get(key)
        if isinstance(raw, str):
            values.extend(_split_free_skill_text(raw))
        else:
            values.extend(_as_string_list(raw))
    return _dedupe_skill_values(values)


def _split_skills(values: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    approved: list[str] = []
    unknown: list[str] = []
    seen_approved: set[str] = set()
    seen_unknown: set[str] = set()

    for value in values:
        cleaned = _clean_text(value)
        if not cleaned:
            continue
        normalized_approved = normalize_skill_token(cleaned, preserve_unknown=False)
        if normalized_approved:
            key = normalized_approved.casefold()
            if key not in seen_approved:
                seen_approved.add(key)
                approved.append(normalized_approved)
            continue
        key = cleaned.casefold()
        if key not in seen_unknown:
            seen_unknown.add(key)
            unknown.append(cleaned)

    return tuple(approved), tuple(unknown)


def _build_system_prompt() -> str:
    return (
        "You extract structured job-description data. "
        "Return only valid JSON. "
        "Do not add markdown, prose, or code fences unless unavoidable. "
        "Never invent missing details. "
        "If a value is missing, use empty strings, empty arrays, null, or 0. "
        "Do not treat locations, visa terms, tax terms, employment terms, years of experience, "
        "or company names as skills."
    )


def _build_user_prompt(
    subject: str,
    body: str,
    *,
    source: str,
    source_hints: dict[str, Any],
) -> str:
    return (
        "Extract a structured job summary as JSON with these fields:\n"
        "{\n"
        '  "role_candidates": string[],\n'
        '  "company": string,\n'
        '  "primary_location": string,\n'
        '  "mentioned_locations": string[],\n'
        '  "work_mode": string,\n'
        '  "salary_text": string,\n'
        '  "visa_hints": string[],\n'
        '  "experience_years_min": number|null,\n'
        '  "skills_text": string,\n'
        '  "f2f_mentioned": boolean,\n'
        '  "asks_contact_fields": boolean,\n'
        '  "is_texas_role": boolean,\n'
        '  "confidence": number,\n'
        '  "evidence": object\n'
        "}\n\n"
        f"Source: {source}\n"
        f"Source hints: {source_hints}\n"
        f"Subject:\n{subject.strip()}\n\n"
        f"Body:\n{body.strip()}\n"
    )


def extract_ai_job_details(
    subject: str,
    body: str,
    *,
    source: str = "gmail",
    source_hints: dict[str, Any] | None = None,
    model_name: str | None = None,
    timeout_seconds: float | None = None,
) -> AIExtractorResult:
    cleaned_subject = _clean_text(subject)
    cleaned_body = _clean_text(body)
    if not cleaned_subject and not cleaned_body:
        return AIExtractorResult()

    hints = {key: value for key, value in dict(source_hints or {}).items() if value not in (None, "", [], {})}
    system_prompt = _build_system_prompt()
    user_prompt = _build_user_prompt(cleaned_subject, cleaned_body, source=source, source_hints=hints)

    try:
        payload = deepseek_json_completion(
            system_prompt,
            user_prompt,
            model_name=model_name,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        return AIExtractorResult(error=str(exc), evidence={"extractor_error": [str(exc)]})

    role_candidates = _as_string_list(payload.get("role_candidates"))
    if not role_candidates:
        fallback_role = _clean_text(payload.get("role"))
        if fallback_role:
            role_candidates = [fallback_role]

    mentioned_locations = _as_string_list(payload.get("mentioned_locations"))
    primary_location = _clean_text(payload.get("primary_location"))
    if primary_location and all(primary_location.casefold() != item.casefold() for item in mentioned_locations):
        mentioned_locations.insert(0, primary_location)

    free_skill_values = list(_collect_free_skill_values(payload))
    skills_text = ", ".join(free_skill_values)
    raw_skill_values = free_skill_values[:]
    skills_approved, skills_unknown = _split_skills(raw_skill_values)

    return AIExtractorResult(
        role_candidates=_dedupe_strings(role_candidates),
        company=_clean_text(payload.get("company")),
        primary_location=primary_location,
        mentioned_locations=_dedupe_strings(mentioned_locations),
        work_mode=_clean_text(payload.get("work_mode")),
        salary_text=_clean_text(payload.get("salary_text")),
        visa_hints=_dedupe_strings(_as_string_list(payload.get("visa_hints"))),
        experience_years_min=_normalize_years(payload.get("experience_years_min")),
        skills_text=skills_text,
        f2f_mentioned=_normalize_bool(payload.get("f2f_mentioned")),
        asks_contact_fields=_normalize_bool(payload.get("asks_contact_fields")),
        is_texas_role=_normalize_bool(payload.get("is_texas_role")),
        skills_approved=skills_approved,
        skills_unknown=skills_unknown,
        confidence=_normalize_confidence(payload.get("confidence")),
        evidence=_normalize_evidence(payload.get("evidence")),
    )


def ai_extractor_result_to_payload(result: AIExtractorResult) -> dict[str, object]:
    payload: dict[str, object] = {
        "role_candidates": list(result.role_candidates),
        "company": result.company,
        "primary_location": result.primary_location,
        "mentioned_locations": list(result.mentioned_locations),
        "work_mode": result.work_mode,
        "salary_text": result.salary_text,
        "visa_hints": list(result.visa_hints),
        "experience_years_min": result.experience_years_min,
        "skills_text": result.skills_text,
        "f2f_mentioned": result.f2f_mentioned,
        "asks_contact_fields": result.asks_contact_fields,
        "is_texas_role": result.is_texas_role,
        "skills_approved": list(result.skills_approved),
        "skills_unknown": list(result.skills_unknown),
        "confidence": result.confidence,
        "evidence": result.evidence,
    }
    if result.error:
        payload["error"] = result.error
    return payload
