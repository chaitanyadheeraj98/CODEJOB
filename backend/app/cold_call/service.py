from __future__ import annotations

from dataclasses import dataclass
import re

from app.ai.deepseek_client import deepseek_chat_completion
from app.ai.truthfulness import enforce_truthfulness
from app.cold_call.prompting import build_cold_call_prompts
from app.cold_call.skill_overlap import find_known_skill_mentions


@dataclass(frozen=True)
class ColdCallContext:
    recruiter_name: str
    recruiter_email: str
    job_title: str
    location: str
    allowed_skill_highlights: str
    allowed_skill_canonicals: tuple[str, ...]
    evidence: str


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _sanitize_script(text: str, resume_text: str) -> str:
    stripped = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    truthful = enforce_truthfulness(stripped, resume_text)
    sentences = _sentence_split(truthful)
    return " ".join(sentences[:4]).strip()


def _fallback_script(context: ColdCallContext) -> str:
    greeting_name = (context.recruiter_name or "").strip()
    salutation = f"Hi {greeting_name}," if greeting_name else "Hi,"
    title = context.job_title or "this role"
    skill_line = (
        f"with resume-backed experience in {context.allowed_skill_highlights}"
        if context.allowed_skill_highlights
        else "with relevant full-stack engineering experience"
    )
    return (
        f"{salutation} this is Chaithanya Dheeraj. "
        f"I am calling about your {title} opening. "
        f"I have 7+ years of experience {skill_line}. "
        "Do you have two minutes to see if my background fits your current requirement?"
    )


def generate_cold_call_script(
    *,
    context: ColdCallContext,
    resume_text: str,
    model_name: str,
) -> str:
    system_prompt, user_prompt = build_cold_call_prompts(
        recruiter_name=context.recruiter_name,
        recruiter_email=context.recruiter_email,
        job_title=context.job_title,
        location=context.location,
        allowed_skill_highlights=context.allowed_skill_highlights,
        evidence=context.evidence,
        resume_text=resume_text,
    )
    try:
        generated = deepseek_chat_completion(system_prompt, user_prompt, model_name=model_name)
        sanitized = _sanitize_script(generated, resume_text)
        mentions = find_known_skill_mentions(sanitized)
        allowed_mentions = set(context.allowed_skill_canonicals) | find_known_skill_mentions(context.job_title)
        disallowed_mentions = mentions - allowed_mentions
        if sanitized and not disallowed_mentions:
            return sanitized
    except Exception:
        pass
    return _sanitize_script(_fallback_script(context), resume_text)
