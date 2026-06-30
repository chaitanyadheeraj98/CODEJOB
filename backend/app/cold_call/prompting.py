from __future__ import annotations


def build_cold_call_prompts(
    *,
    recruiter_name: str,
    recruiter_email: str,
    job_title: str,
    location: str,
    allowed_skill_highlights: str,
    evidence: str,
    resume_text: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are an expert recruiter cold-call coach. "
        "Create ultra-short, high-impact phone openers grounded strictly in the supplied resume and job details only."
    )

    user_prompt = f"""
Task:
Write a punchy, conversational cold-call opener script for a candidate calling a recruiter.

Strict Rules:
1) Output plain text only. Do not use bullets, quotes, brackets, markdown, or placeholders.
2) Maximum 4 short sentences total.
3) Follow this structure:
   - Sentence 1: State the candidate name and exact reason for calling, using the job title or role context.
   - Sentence 2: State experience level and one strong proof point from the resume.
   - Sentence 3-4: Ask for a brief, low-friction conversation about fit and next steps.
4) Avoid dense comma-separated technology lists.
5) You may mention technical skills ONLY from Allowed Skill Highlights.
6) If Allowed Skill Highlights is empty, do not mention specific tools. Use a broader verified experience statement from the resume.
7) Do not mention skills that appear only in the job requirement or only in the resume.
8) Do not say "matches exactly" unless the evidence is explicit.
9) Do not claim years, tools, domains, employers, or project experience not present in the resume context.
10) Do not claim Spring AOP/Aspect-Oriented Programming unless explicitly present in resume context.
11) Tone: professional, confident, clear, and natural for a spoken phone call.

Job Signal:
- Recruiter Name: {recruiter_name}
- Recruiter Email: {recruiter_email}
- Job Title: {job_title}
- Location: {location}
- Allowed Skill Highlights: {allowed_skill_highlights or "None"}
- Evidence: {evidence}

Candidate Resume Context:
{resume_text}

Return only the final spoken script text.
""".strip()

    return system_prompt, user_prompt
