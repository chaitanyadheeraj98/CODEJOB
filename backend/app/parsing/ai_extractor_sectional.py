from __future__ import annotations

import logging
from typing import TypeAlias

from instructor.core.exceptions import IncompleteOutputException, InstructorError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.deepseek_client import build_deepseek_instructor_client
from app.config import settings


logger = logging.getLogger(__name__)

AI_EXTRACTOR_SECTION_BASE_MAX_TOKENS = 700
AI_EXTRACTOR_SECTION_MAX_ATTEMPTS = 3


class RoleLocationSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role_candidates: list[str] = Field(default_factory=list)
    company: str = ""
    primary_location: str = ""
    mentioned_locations: list[str] = Field(default_factory=list)
    work_mode: str = ""
    is_texas_role: bool = False
    evidence: dict[str, list[str]] = Field(default_factory=dict)


class CompensationExperienceSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    salary_text: str = ""
    visa_hints: list[str] = Field(default_factory=list)
    experience_years_min: int | None = None
    evidence: dict[str, list[str]] = Field(default_factory=dict)


class SkillsSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    skills_text: str = ""
    skills: list[str] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    excluded_skills: list[str] = Field(default_factory=list)
    evidence: dict[str, list[str]] = Field(default_factory=dict)


class FlagsConfidenceSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    f2f_mentioned: bool = False
    asks_contact_fields: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: dict[str, list[str]] = Field(default_factory=dict)


SectionSpec: TypeAlias = tuple[str, type[BaseModel], str]

SECTION_SPECS: tuple[SectionSpec, ...] = (
    (
        "role_location",
        RoleLocationSection,
        "Return only role_candidates, company, primary_location, mentioned_locations, work_mode, "
        "is_texas_role, and short direct evidence for those fields.",
    ),
    (
        "compensation_experience",
        CompensationExperienceSection,
        "Return only salary_text, visa_hints, experience_years_min, and short direct evidence for those fields.",
    ),
    (
        "skills",
        SkillsSection,
        "Return only skills_text, skills, must_have_skills, nice_to_have_skills, excluded_skills, "
        "and short direct evidence for those fields. Extract every explicit skill once.",
    ),
    (
        "flags_confidence",
        FlagsConfidenceSection,
        "Return only f2f_mentioned, asks_contact_fields, confidence, and short direct evidence for those fields.",
    ),
)


def _error_category(exc: Exception) -> str:
    if isinstance(exc, IncompleteOutputException):
        return "truncated_json"
    if isinstance(exc, ValidationError):
        return "schema_failure"
    if isinstance(exc, InstructorError):
        return "provider_failure"
    return "provider_error"


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, IncompleteOutputException):
        return "truncated JSON content"
    if isinstance(exc, ValidationError):
        return "section schema validation failed"
    if isinstance(exc, InstructorError):
        return "section provider request failed"
    return "section extraction failed"


def extract_sections(
    system_prompt: str,
    user_prompt: str,
    *,
    source: str,
    model_name: str | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, object]:
    client = build_deepseek_instructor_client(timeout_seconds=timeout_seconds)
    merged: dict[str, object] = {}
    merged_evidence: dict[str, list[str]] = {}

    for section_name, response_model, instruction in SECTION_SPECS:
        last_error: Exception | None = None
        for attempt in range(AI_EXTRACTOR_SECTION_MAX_ATTEMPTS):
            max_tokens = AI_EXTRACTOR_SECTION_BASE_MAX_TOKENS * (2**attempt)
            try:
                section, _response = client.create_with_completion(
                    model=model_name or settings.deepseek_model_fast or "deepseek-v4-flash",
                    messages=[
                        {
                            "role": "system",
                            "content": f"{system_prompt}\nRecovery section: {instruction}",
                        },
                        {"role": "user", "content": user_prompt},
                    ],
                    response_model=response_model,
                    max_retries=1,
                    temperature=0.0,
                    max_tokens=max_tokens,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                section_payload = section.model_dump(mode="json")
                evidence = section_payload.pop("evidence", {})
                merged.update(section_payload)
                if isinstance(evidence, dict):
                    merged_evidence.update(evidence)
                break
            except (IncompleteOutputException, ValidationError, InstructorError) as exc:
                last_error = exc
        else:
            failure = last_error or RuntimeError("section extraction failed")
            logger.warning(
                "ai_extractor_sectional_failed section=%s category=%s",
                section_name,
                _error_category(failure),
            )
            raise RuntimeError(
                f"{section_name} section failed after {AI_EXTRACTOR_SECTION_MAX_ATTEMPTS} attempts: "
                f"{_safe_error_message(failure)}"
            ) from failure

    merged["evidence"] = merged_evidence
    logger.info(
        "ai_extractor_sectional_used section_count=%s email_source=%s",
        len(SECTION_SPECS),
        source,
    )
    return merged
