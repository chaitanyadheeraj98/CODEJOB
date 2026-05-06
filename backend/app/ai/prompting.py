from __future__ import annotations


def build_reply_prompts(
    *,
    sender: str,
    subject: str,
    body: str,
    role: str,
    location: str,
    salary_text: str,
    skills_text: str,
    resume_text: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are an expert job applicant assistant. "
        "Write a natural human plain-text email reply to a recruiter. "
        "Do not use markdown, bullets with symbols, or placeholders. "
        "Do not invent facts not present in resume context. "
        "Keep the tone professional, warm, concise, and specific to the role."
    )

    user_prompt = f"""
Recruiter Sender: {sender}
Recruiter Subject: {subject}

Parsed Role: {role}
Parsed Location: {location}
Parsed Salary: {salary_text}
Parsed Skills: {skills_text}

Recruiter Email Content:
{body}

Candidate Resume Context:
{resume_text}

Write only the email body text that the candidate should send.
Constraints:
1) Start with a greeting (for example: Hi,).
2) Mention direct interest in this specific role.
3) Mention relevant experience/skills supported by resume context.
4) Mention attached resume naturally.
5) End with a professional sign-off.
6) Plain text only, no markdown characters like ** or bullet symbols.
""".strip()

    return system_prompt, user_prompt

