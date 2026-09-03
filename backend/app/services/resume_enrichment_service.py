from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, ConfigDict, Field

from app.ai.chat.history import message_text
from app.ai.chat.llm import build_chat_llm
from app.models import ResumeAsset
from app.parsing.document_extraction import extract_document_text


logger = logging.getLogger(__name__)

_SUMMARY_PROMPT = """Summarize this resume for comparing it with job requirements.
Use at most 120 words. Mention role focus, strongest skills, experience, domains,
and certifications. Do not add facts that are not in the resume.

Resume:
{text}
"""

_EVIDENCE_PROMPT = """Extract resume evidence as JSON only, with this exact shape:
{{"skills":[{{"name":"Spring Boot","evidence":"Built Spring Boot services at ABC."}}],
"years_detected":6,"titles":["Senior Backend Engineer"],
"certifications":["AWS Certified Developer"],"projects":["Built ..."],"domain":"fintech"}}
Use only facts stated in the resume. Use null for unknown years, empty arrays for
unknown lists, and an empty string for an unknown domain.

Resume:
{text}
"""


class ResumeSkillEvidence(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    evidence: str = Field(default="", max_length=1000)

    model_config = ConfigDict(extra="ignore")


class ResumeEvidence(BaseModel):
    skills: list[ResumeSkillEvidence] = Field(default_factory=list)
    years_detected: int | None = Field(default=None, ge=0, le=80)
    titles: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    domain: str = ""

    model_config = ConfigDict(extra="ignore")


def _validate_evidence_json(raw: str) -> ResumeEvidence:
    text = (raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        payload = json.loads(text)
        return ResumeEvidence.model_validate(payload)
    except Exception as exc:
        logger.warning("Resume evidence JSON was invalid: %s", exc)
        return ResumeEvidence()


def _apply_role_and_label(resume: ResumeAsset, evidence: ResumeEvidence) -> None:
    if not getattr(resume, "primary_role", "") and evidence.titles:
        resume.primary_role = evidence.titles[0].strip()[:255]
    if not getattr(resume, "variant_label", "") and evidence.domain:
        resume.variant_label = evidence.domain.strip()[:120]


def backfill_role_and_label(resume: ResumeAsset) -> bool:
    """Fill primary_role/variant_label from evidence already on the resume. No LLM call."""
    before = (resume.primary_role, resume.variant_label)
    raw = str(getattr(resume, "content_evidence_json", "") or "").strip()
    if raw:
        try:
            _apply_role_and_label(resume, ResumeEvidence.model_validate(json.loads(raw)))
        except Exception as exc:
            logger.warning("Resume evidence JSON was invalid during label backfill: %s", exc)
    return (resume.primary_role, resume.variant_label) != before


def enrich_resume(resume: ResumeAsset) -> None:
    extracted = extract_document_text(resume.file_path, resume.file_name, max_chars=50000)
    resume.content_markdown = extracted.markdown_text
    resume.content_summary = None
    resume.content_evidence_json = "{}"

    try:
        llm = build_chat_llm()
    except Exception as exc:
        logger.warning("Resume enrichment model unavailable: %s", exc)
        return

    try:
        response = llm.invoke(_SUMMARY_PROMPT.format(text=resume.content_markdown))
        resume.content_summary = message_text(response.content).strip() or None
    except Exception as exc:
        logger.warning("Resume summary generation skipped: %s", exc)

    try:
        response = llm.invoke(_EVIDENCE_PROMPT.format(text=resume.content_markdown))
        evidence = _validate_evidence_json(message_text(response.content))
        resume.content_evidence_json = evidence.model_dump_json(exclude_none=False)
        _apply_role_and_label(resume, evidence)
    except Exception as exc:
        logger.warning("Resume evidence generation skipped: %s", exc)

