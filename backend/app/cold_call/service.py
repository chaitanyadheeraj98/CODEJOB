from __future__ import annotations

from dataclasses import dataclass
import re

from app.ai.deepseek_client import deepseek_chat_completion
from app.cold_call.prompting import build_cold_call_prompts


@dataclass(frozen=True)
class ColdCallContext:
    recruiter_email: str
    job_title: str
    location: str
    skills: str
    evidence: str


def _sentence_split(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _max_claimed_years_from_resume(resume_text: str) -> int | None:
    matches = re.findall(r"\b(\d{1,2})\s*\+?\s*years?\b", resume_text or "", flags=re.IGNORECASE)
    values = [int(value) for value in matches if value.isdigit()]
    if not values:
        return None
    return max(values)


def _enforce_truthfulness(text: str, resume_text: str) -> str:
    cleaned = text
    max_years = _max_claimed_years_from_resume(resume_text)
    if max_years is not None:
        def _cap_years(match: re.Match[str]) -> str:
            claimed = int(match.group(1))
            if claimed > max_years:
                return f"{max_years}+ years"
            suffix = "+" if "+" in match.group(0) else ""
            return f"{claimed}{suffix} years"

        cleaned = re.sub(r"\b(\d{1,2})\s*\+?\s*years\b", _cap_years, cleaned, flags=re.IGNORECASE)

    resume_lower = (resume_text or "").lower()
    if "aop" not in resume_lower and "aspect oriented" not in resume_lower:
        cleaned = re.sub(
            r"\bSpring\s+Aspect\s+Oriented\s+Programming\b",
            "Spring-based transaction management and security implementations",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"\bSpring\s+AOP\b",
            "Spring-based transaction management and security implementations",
            cleaned,
            flags=re.IGNORECASE,
        )
    return cleaned


def _sanitize_script(text: str, resume_text: str) -> str:
    stripped = " ".join(line.strip() for line in (text or "").splitlines() if line.strip())
    truthful = _enforce_truthfulness(stripped, resume_text)
    sentences = _sentence_split(truthful)
    return " ".join(sentences[:5]).strip()


def _fallback_script(context: ColdCallContext) -> str:
    title = context.job_title or "this role"
    location = context.location or "your opening"
    return (
        f"Hi, this is Chaithanya Dheeraj and I am calling about {title} in {location}. "
        "I have 7+ years of full-stack Java experience and currently work at Centier Bank. "
        f"My background aligns with the core stack you mentioned, including {context.skills or 'Java and Spring technologies'}. "
        "I wanted to quickly check if this role is still active and whether my profile is a fit. "
        "If this sounds relevant, I would appreciate a short discussion on next steps."
    )


def generate_cold_call_script(
    *,
    context: ColdCallContext,
    resume_text: str,
    model_name: str,
) -> str:
    system_prompt, user_prompt = build_cold_call_prompts(
        recruiter_email=context.recruiter_email,
        job_title=context.job_title,
        location=context.location,
        skills=context.skills,
        evidence=context.evidence,
        resume_text=resume_text,
    )
    try:
        generated = deepseek_chat_completion(system_prompt, user_prompt)
        sanitized = _sanitize_script(generated, resume_text)
        if sanitized:
            return sanitized
    except Exception:
        pass
    return _sanitize_script(_fallback_script(context), resume_text)

