from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DraftResumeContextStatus = Literal[
    "injected",
    "limited",
    "missing_resume",
    "extract_failed",
    "rules_only",
]

RESUME_CONTEXT_INJECTED: DraftResumeContextStatus = "injected"
RESUME_CONTEXT_LIMITED: DraftResumeContextStatus = "limited"
RESUME_CONTEXT_MISSING: DraftResumeContextStatus = "missing_resume"
RESUME_CONTEXT_EXTRACT_FAILED: DraftResumeContextStatus = "extract_failed"
RESUME_CONTEXT_RULES_ONLY: DraftResumeContextStatus = "rules_only"

_LIMITED_EXTRACTION_MARKER = "text extraction is limited"


@dataclass(frozen=True)
class ResumeContextAttribution:
    status: DraftResumeContextStatus
    extracted_chars: int


def classify_extracted_resume_context(
    resume_text: str | None,
    *,
    extraction_error: str | None = None,
) -> ResumeContextAttribution:
    if extraction_error:
        return ResumeContextAttribution(status=RESUME_CONTEXT_EXTRACT_FAILED, extracted_chars=0)
    text = (resume_text or "").strip()
    if not text:
        return ResumeContextAttribution(status=RESUME_CONTEXT_EXTRACT_FAILED, extracted_chars=0)
    if _LIMITED_EXTRACTION_MARKER in text.lower():
        return ResumeContextAttribution(status=RESUME_CONTEXT_LIMITED, extracted_chars=len(text))
    return ResumeContextAttribution(status=RESUME_CONTEXT_INJECTED, extracted_chars=len(text))
