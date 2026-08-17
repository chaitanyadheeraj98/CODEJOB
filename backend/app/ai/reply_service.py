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
from app.phase0 import DEFAULT_SIGNATURE_EMAIL, DEFAULT_SIGNATURE_NAME, DEFAULT_SIGNATURE_PHONE


@dataclass
class ReplyGenerationResult:
    draft_text: str
    source: str
    ai_model: str | None
    ai_error: str | None
    resume_context_status: DraftResumeContextStatus


DEFAULT_SIGNATURE_BLOCK = "\n".join(
    [
        "Best regards,",
        DEFAULT_SIGNATURE_NAME,
        f"[PHONE] {DEFAULT_SIGNATURE_PHONE}",
        f"[EMAIL] {DEFAULT_SIGNATURE_EMAIL}",
    ]
)


def _sanitize_plain_text_reply(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw in text.splitlines():
        cleaned_lines.append(raw.strip())
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned


def _extract_subject_line(text: str) -> str | None:
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.lower().startswith("subject:"):
            return stripped
    return None


def _extract_signature_block(fallback_draft: str) -> str:
    lines = fallback_draft.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        if lines[idx].strip().lower() == "best regards,":
            block = "\n".join(line.strip() for line in lines[idx:] if line.strip()).strip()
            if block:
                return block
    return DEFAULT_SIGNATURE_BLOCK


def _is_greeting_like(stripped: str) -> bool:
    lowered = stripped.lower()
    return lowered.startswith("hi ") or lowered == "hi" or lowered.startswith("hello") or lowered.startswith("dear ")


def _extract_body_only(ai_text: str, expected_greeting: str) -> str:
    body_lines: list[str] = []
    started_body = False
    expected_greeting_lower = expected_greeting.strip().lower()

    for raw in ai_text.splitlines():
        stripped = raw.strip()
        lowered = stripped.lower()
        if not started_body:
            if not stripped:
                continue
            if lowered.startswith("subject:"):
                continue
            if lowered.startswith("nvoids listing:"):
                continue
            if lowered == expected_greeting_lower or _is_greeting_like(stripped):
                continue
            started_body = True

        if lowered == "best regards," or lowered.startswith("best regards"):
            break
        if lowered.startswith("nvoids listing:"):
            continue
        body_lines.append(stripped)

    return "\n".join(body_lines).strip()


def _compose_reply_with_fixed_wrapper(
    ai_text: str,
    *,
    expected_greeting: str,
    fallback_draft: str,
) -> str:
    subject_line = _extract_subject_line(ai_text) or _extract_subject_line(fallback_draft)
    body_text = _extract_body_only(ai_text, expected_greeting)
    signature_block = _extract_signature_block(fallback_draft)

    if not subject_line or not body_text:
        return fallback_draft.strip()

    return "\n\n".join(
        [
            subject_line.strip(),
            expected_greeting.strip(),
            body_text,
            signature_block,
        ]
    ).strip()


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
        sanitized = _compose_reply_with_fixed_wrapper(
            sanitized,
            expected_greeting=greeting_line,
            fallback_draft=fallback_draft,
        )
        if sanitized == fallback_draft.strip():
            raise RuntimeError("AI returned unusable draft after sanitation")
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
