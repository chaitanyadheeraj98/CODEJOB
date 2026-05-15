from __future__ import annotations


def build_cold_call_prompts(
    *,
    recruiter_email: str,
    job_title: str,
    location: str,
    skills: str,
    evidence: str,
    resume_text: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are a concise recruiter cold-call assistant. "
        "Create short, truthful phone openers grounded in the supplied resume and job posting details only."
    )

    user_prompt = f"""
Task:
Write a cold-call opener script for the candidate to call the recruiter.

Strict Rules:
1) Output plain text only.
2) Maximum 5 sentences total.
3) Do not claim years, tools, or project experience not present in the resume context.
4) Do not claim Spring AOP/Aspect-Oriented Programming unless explicitly present in resume context.
5) Keep tone confident, polite, and conversational.
6) Include a clear close asking for a brief discussion about fit/next steps.

Job Signal:
- Recruiter Email: {recruiter_email}
- Job Title: {job_title}
- Location: {location}
- Skills: {skills}
- Evidence: {evidence}

Candidate Resume Context:
{resume_text}

Return only the final script text.
""".strip()

    return system_prompt, user_prompt

