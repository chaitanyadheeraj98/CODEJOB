from __future__ import annotations


def build_reply_prompts(
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
    resume_text: str,
) -> tuple[str, str]:
    system_prompt = (
        "You are my personal job application email assistant. "
        "Follow the behavior contract and output format exactly. "
        "Do not invent resume facts, employers, or years not supported by context."
    )

    user_prompt = f"""
Task:
Convert the job posting I provide into a clean, professional, recruiter-ready email.

Qualifier Note:
The backend already enforced F2F/Texas gating. Generate an email draft for this qualified input.

Strict Instructions:
1) Use a professional tone.
2) Keep it concise but strong (not too long).
3) Align experience specifically to the job description.
4) Highlight matching technologies using **bold formatting**.
5) Mention current role as Full Stack Developer at Centier Bank (banking domain).
6) Mention 7+ years of experience unless JD strictly requires otherwise.
7) Do NOT add Visa or Location details unless explicitly asked in the job description.
8) If recruiter asks for specific fields (Visa, Location, etc.), include:
   - Visa: H1B
   - Current Location: Dallas, TX
9) Always end with this exact signature block:
Best regards,
Chaithanya Dheeraj N
[PHONE] +1 940-629-6920
[EMAIL] chaithanyadheeraj1026@gmail.com

Greeting Rule (highest priority):
- Use this greeting exactly as the first greeting line: "{greeting_line}"
- Do not greet sender/CC names unless they are the same as recruiter TO contact.
- If the greeting is "Hi,", keep it exactly as "Hi,".

Output Format:
- Subject line
- Greeting
- 2-4 strong paragraphs
- Bullet points for technical alignment (if needed)
- Closing
- Signature

Recruiter Sender Header: {sender}
Resolved Recruiter TO Contact: {recruiter_to_email or "unknown"}
Recruiter Subject: {subject}
Parsed Role: {role}
Parsed Location: {location}
Parsed Salary: {salary_text}
Parsed Skills: {skills_text}
Expected Greeting: {greeting_line}

Recruiter Email Content:
{body}

Candidate Resume Context:
{resume_text}

Return only the final email draft text, with no extra commentary.
""".strip()

    return system_prompt, user_prompt
