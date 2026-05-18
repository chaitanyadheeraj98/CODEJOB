from __future__ import annotations

from dataclasses import dataclass

from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_EXTRACT_FAILED,
    DraftResumeContextStatus,
    classify_extracted_resume_context,
)
from app.ai.deepseek_client import deepseek_chat_completion
from app.ai.prompting import build_reply_prompts
from app.ai.resume_context import extract_resume_context


@dataclass
class ReplyGenerationResult:
    draft_text: str
    source: str
    ai_model: str | None
    ai_error: str | None
    resume_context_status: DraftResumeContextStatus


def _sanitize_plain_text_reply(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw in text.splitlines():
        cleaned_lines.append(raw.strip())
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned


def _enforce_greeting_line(draft_text: str, expected_greeting: str) -> str:
    lines = draft_text.splitlines()
    subject_idx: int | None = None
    first_greeting_idx: int | None = None

    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped:
            continue
        if subject_idx is None and stripped.lower().startswith("subject:"):
            subject_idx = idx
        if stripped.lower().startswith("hi"):
            first_greeting_idx = idx
            break

    # Remove all greeting-like lines to avoid duplicates, then insert a single canonical greeting.
    cleaned_lines = [line for line in lines if not line.strip().lower().startswith("hi")]

    if first_greeting_idx is not None:
        insert_at = first_greeting_idx
    elif subject_idx is not None:
        insert_at = min(subject_idx + 1, len(cleaned_lines))
    else:
        insert_at = 0

    while insert_at > 0 and insert_at <= len(cleaned_lines) and cleaned_lines[insert_at - 1].strip() == "":
        insert_at -= 1

    normalized = cleaned_lines[:insert_at] + [expected_greeting] + cleaned_lines[insert_at:]
    return "\n".join(normalized).strip()


def generate_reply_with_ai_or_fallback(
    *,
    sender: str,
    recruiter_to_email: str | None,
    greeting_line: str,
    subject: str,
    body: str,
    role: str,
    location: str,
    salary_text: str,
    skills_text: str,
    resume_path: str,
    resume_file_name: str,
    fallback_draft: str,
    model_name: str,
) -> ReplyGenerationResult:
    resume_text = ""
    context_status: DraftResumeContextStatus = RESUME_CONTEXT_EXTRACT_FAILED
    try:
        resume_text = extract_resume_context(resume_path, resume_file_name)
        context_status = classify_extracted_resume_context(resume_text).status
    except Exception:
        context_status = RESUME_CONTEXT_EXTRACT_FAILED
    system_prompt, user_prompt = build_reply_prompts(
        sender=sender,
        recruiter_to_email=recruiter_to_email,
        greeting_line=greeting_line,
        subject=subject,
        body=body,
        role=role,
        location=location,
        salary_text=salary_text,
        skills_text=skills_text,
        resume_text=resume_text,
    )
    try:
        generated = deepseek_chat_completion(system_prompt, user_prompt)
        sanitized = _sanitize_plain_text_reply(generated)
        sanitized = _enforce_greeting_line(sanitized, greeting_line)
        if not sanitized:
            raise RuntimeError("AI returned empty draft after sanitation")
        return ReplyGenerationResult(
            draft_text=sanitized,
            source="deepseek",
            ai_model=model_name,
            ai_error=None,
            resume_context_status=context_status,
        )
    except Exception as exc:
        return ReplyGenerationResult(
            draft_text=fallback_draft,
            source="rules_only",
            ai_model=model_name,
            ai_error=str(exc),
            resume_context_status=context_status,
        )
