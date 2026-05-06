from __future__ import annotations

from dataclasses import dataclass

from app.ai.deepseek_client import deepseek_chat_completion
from app.ai.prompting import build_reply_prompts
from app.ai.resume_context import extract_resume_context


@dataclass
class ReplyGenerationResult:
    draft_text: str
    source: str
    ai_model: str | None
    ai_error: str | None


def _sanitize_plain_text_reply(text: str) -> str:
    cleaned_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.replace("**", "").replace("__", "").strip()
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].strip()
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    return cleaned


def generate_reply_with_ai_or_fallback(
    *,
    sender: str,
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
    resume_text = extract_resume_context(resume_path, resume_file_name)
    system_prompt, user_prompt = build_reply_prompts(
        sender=sender,
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
        if not sanitized:
            raise RuntimeError("AI returned empty draft after sanitation")
        return ReplyGenerationResult(
            draft_text=sanitized,
            source="deepseek",
            ai_model=model_name,
            ai_error=None,
        )
    except Exception as exc:
        return ReplyGenerationResult(
            draft_text=fallback_draft,
            source="rules_only",
            ai_model=model_name,
            ai_error=str(exc),
        )

