import re


SKILL_KEYWORDS = [
    "python",
    "fastapi",
    "sql",
    "postgres",
    "sqlite",
    "react",
    "typescript",
    "aws",
    "docker",
]


def _extract_location(text: str) -> str:
    match = re.search(r"(remote|hybrid|onsite|on-site)", text, re.IGNORECASE)
    return match.group(1).lower() if match else "unknown"


def _extract_salary(text: str) -> str:
    match = re.search(r"(\$?\d{2,3}[,]?\d{0,3}\s?-\s?\$?\d{2,3}[,]?\d{0,3})", text)
    return match.group(1) if match else "not_specified"


def _extract_role(subject: str, body: str) -> str:
    combined = f"{subject} {body}"
    role_patterns = [
        r"(software engineer)",
        r"(backend engineer)",
        r"(frontend engineer)",
        r"(full[- ]stack engineer)",
        r"(python developer)",
    ]
    for pattern in role_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return match.group(1).title()
    return "Unknown Role"


def _extract_skills(text: str) -> str:
    lower = text.lower()
    hits = [skill for skill in SKILL_KEYWORDS if skill in lower]
    return ", ".join(hits) if hits else "none_detected"


def score_email(subject: str, body: str) -> tuple[int, str]:
    text = f"{subject} {body}".lower()
    score = 50

    if "remote" in text:
        score += 15
    if "senior" in text or "staff" in text:
        score += 10
    if "contract" in text:
        score -= 10
    if "visa required" in text:
        score -= 20

    skill_hits = sum(1 for skill in SKILL_KEYWORDS if skill in text)
    score += min(skill_hits * 4, 20)
    score = max(0, min(score, 100))

    if score >= 85:
        decision = "Qualified"
    elif score >= 60:
        decision = "Maybe"
    else:
        decision = "Reject"
    return score, decision


def draft_reply(sender: str, role: str, decision: str) -> str:
    if decision == "Qualified":
        return (
            f"Hi {sender},\n\n"
            f"Thanks for reaching out about the {role} role. "
            "This looks aligned with my profile. Could you share the full JD, team details, "
            "and interview process?\n\nBest,\nChaitanya"
        )
    if decision == "Maybe":
        return (
            f"Hi {sender},\n\n"
            f"Thanks for contacting me about the {role} role. "
            "I am interested in reviewing more details before deciding. "
            "Please share the full job description, location expectations, and compensation range.\n\n"
            "Best,\nChaitanya"
        )
    return (
        f"Hi {sender},\n\n"
        "Thank you for reaching out. At the moment this opportunity does not look like the right fit. "
        "Please feel free to keep me in mind for future roles that align more closely.\n\n"
        "Best,\nChaitanya"
    )


def parse_and_classify(subject: str, body: str) -> dict[str, str | int]:
    role = _extract_role(subject, body)
    location = _extract_location(body)
    salary_text = _extract_salary(body)
    skills_text = _extract_skills(f"{subject} {body}")
    score, decision = score_email(subject, body)
    return {
        "role": role,
        "location": location,
        "salary_text": salary_text,
        "skills_text": skills_text,
        "score": score,
        "decision": decision,
    }

