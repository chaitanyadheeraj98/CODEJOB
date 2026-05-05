import re

from app.models import UserSettings

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

RECRUITER_HINTS = [
    "recruiter",
    "recruiting",
    "talent",
    "hiring",
    "staffing",
    "sourcer",
]

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TEXAS_RE = re.compile(r"\b(tx|texas)\b", re.IGNORECASE)
F2F_RE = re.compile(r"\b(face[- ]to[- ]face|f2f)\b", re.IGNORECASE)
EMPLOYER_DOMAIN = "horizonsofttech.net"


def is_recruiter_like(sender: str, subject: str, body: str) -> bool:
    combined = f"{sender} {subject} {body}".lower()
    return any(hint in combined for hint in RECRUITER_HINTS)


def extract_employer_email(body: str, recruiter_email: str | None = None) -> str | None:
    matches = EMAIL_RE.findall(body)
    cleaned: list[str] = []
    recruiter_lower = recruiter_email.lower() if recruiter_email else ""
    for email in matches:
        e = email.strip().lower()
        if recruiter_lower and e == recruiter_lower:
            continue
        cleaned.append(e)
    return cleaned[0] if cleaned else None


def resolve_to_cc(sender: str, subject: str, body: str, snippet: str = "") -> tuple[str | None, str | None]:
    sender_email_match = EMAIL_RE.search(sender)
    sender_email = sender_email_match.group(0).lower() if sender_email_match else sender.strip().lower()
    combined_text = f"{subject}\n{body}\n{snippet}"
    all_emails = [e.lower() for e in EMAIL_RE.findall(combined_text)]
    unique_emails: list[str] = []
    for e in all_emails:
        if e not in unique_emails:
            unique_emails.append(e)

    employer_candidates = [e for e in unique_emails if e.endswith(f"@{EMPLOYER_DOMAIN}")]
    recruiter_candidates = [
        e for e in unique_emails if not e.endswith(f"@{EMPLOYER_DOMAIN}") and not e.endswith("@googlegroups.com")
    ]

    # Prefer recruiter address from forwarded headers: "From: Name <recruiter@domain>"
    forwarded_from_matches = re.findall(r"(?im)^\s*from\s*:\s*.*?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", combined_text)
    forwarded_non_employer = [
        e.lower()
        for e in forwarded_from_matches
        if not e.lower().endswith(f"@{EMPLOYER_DOMAIN}") and not e.lower().endswith("@googlegroups.com")
    ]

    if sender_email and sender_email.endswith(f"@{EMPLOYER_DOMAIN}"):
        if sender_email not in employer_candidates:
            employer_candidates.insert(0, sender_email)
    elif sender_email:
        if sender_email not in recruiter_candidates:
            recruiter_candidates.insert(0, sender_email)

    to_email = forwarded_non_employer[0] if forwarded_non_employer else (recruiter_candidates[0] if recruiter_candidates else None)
    cc_email = employer_candidates[0] if employer_candidates else None
    return to_email, cc_email


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


def _extract_location_text(subject: str, body: str) -> str:
    combined = f"{subject}\n{body}"
    line_hit = re.search(r"(?im)^\s*(location|job location)\s*[:\-]\s*(.+)$", combined)
    if line_hit:
        return line_hit.group(2).strip()

    city_state = re.search(r"\b([A-Za-z .'-]+,\s*[A-Z]{2})\b", combined)
    if city_state:
        return city_state.group(1).strip()

    texas_hit = TEXAS_RE.search(combined)
    if texas_hit:
        return "Texas"
    return "unknown"


def _needs_contact_fields(body: str) -> bool:
    lower = body.lower()
    keywords = [
        "visa status",
        "work authorization",
        "visa",
        "current location",
        "location",
        "where are you located",
        "share your details",
    ]
    return any(k in lower for k in keywords)


def _parse_salary_floor(salary_text: str) -> int | None:
    nums = re.findall(r"\d{2,3}[,]?\d{0,3}", salary_text)
    if not nums:
        return None
    try:
        return int(nums[0].replace(",", ""))
    except ValueError:
        return None


def hard_filter_check(parsed: dict[str, str | int], settings: UserSettings) -> tuple[bool, str]:
    reasons: list[str] = []

    if settings.accepted_locations:
        accepted = [loc.strip().lower() for loc in settings.accepted_locations.split(",") if loc.strip()]
        if accepted and str(parsed["location"]).lower() not in accepted and "any" not in accepted:
            reasons.append("location_mismatch")

    if settings.min_salary is not None:
        salary_floor = _parse_salary_floor(str(parsed["salary_text"]))
        if salary_floor is not None and salary_floor < settings.min_salary:
            reasons.append("salary_below_min")

    combined = f"{parsed['role']} {parsed['skills_text']}".lower()
    must_have_skills = [s.strip().lower() for s in settings.must_have_skills.split(",") if s.strip()]
    missing = [skill for skill in must_have_skills if skill not in combined]
    if missing:
        reasons.append(f"missing_skills:{'|'.join(missing)}")

    if reasons:
        return False, ", ".join(reasons)
    return True, "hard_filters_passed"


def ai_assist_score(parsed: dict[str, str | int], settings: UserSettings) -> tuple[float, str]:
    text = f"{parsed['role']} {parsed['skills_text']} {settings.free_text_guidance}".lower()
    score = 0.45

    role_keywords = [k.strip().lower() for k in settings.role_keywords.split(",") if k.strip()]
    if role_keywords:
        hits = sum(1 for k in role_keywords if k in text)
        score += min(hits * 0.08, 0.24)

    skill_hits = sum(1 for skill in SKILL_KEYWORDS if skill in text)
    score += min(skill_hits * 0.03, 0.21)
    score = max(0.0, min(score, 1.0))
    return score, f"AI fit score computed from role keywords and skill overlap ({score:.2f})"


def should_block_f2f(parsed: dict[str, str | int | bool]) -> tuple[bool, str]:
    f2f_mentioned = bool(parsed.get("f2f_mentioned", False))
    if not f2f_mentioned:
        return False, ""
    location_text = str(parsed.get("job_location_text", "unknown"))
    is_texas = bool(parsed.get("is_texas_role", False))
    if not is_texas:
        return True, f"F2F mentioned but non-Texas location ({location_text})"
    return False, ""


def _bold_skill(skill: str) -> str:
    return f"**{skill.upper()}**"


def draft_reply(sender: str, role: str, parsed: dict[str, str | int | bool]) -> str:
    include_contact_fields = bool(parsed.get("asks_contact_fields", False))
    matched_skills = str(parsed.get("skills_text", "none_detected"))
    skills = [s.strip() for s in matched_skills.split(",") if s.strip() and s.strip() != "none_detected"]
    bold_skills = ", ".join(_bold_skill(s) for s in skills[:6]) if skills else "**FULL-STACK DEVELOPMENT**"
    greeting = "Hi,"
    subject_line = f"Subject: Application for {role} - 7+ Years Full Stack Experience"

    body_lines = [
        subject_line,
        "",
        greeting,
        "",
        f"Thank you for sharing the {role} opportunity. I am interested in this role and bring 7+ years of experience building and delivering enterprise applications.",
        "I am currently working as a Full Stack Developer at Centier Bank in the banking domain, where I design and implement end-to-end solutions across backend services and modern web interfaces.",
        "My background aligns well with your requirements, especially across the following technologies:",
        f"- {bold_skills}",
        "I have attached my resume for your review and would be glad to discuss how my experience matches your team's needs.",
    ]

    if include_contact_fields:
        body_lines.append("Requested details: Visa: H1B | Current Location: Dallas, TX")

    body_lines.extend(
        [
            "",
            "Best regards,  ",
            "Chaithanya Dheeraj N  ",
            "📞 +1 940-629-6920  ",
            "✉️ chaithanyadheeraj1026@gmail.com  ",
        ]
    )
    return "\n".join(body_lines)


def parse_email(subject: str, body: str) -> dict[str, str | int]:
    role = _extract_role(subject, body)
    location = _extract_location(body)
    salary_text = _extract_salary(body)
    skills_text = _extract_skills(f"{subject} {body}")
    location_text = _extract_location_text(subject, body)
    f2f_mentioned = bool(F2F_RE.search(f"{subject}\n{body}"))
    asks_contact_fields = _needs_contact_fields(body)
    is_texas_role = bool(TEXAS_RE.search(location_text))
    return {
        "role": role,
        "location": location,
        "job_location_text": location_text,
        "salary_text": salary_text,
        "skills_text": skills_text,
        "f2f_mentioned": f2f_mentioned,
        "asks_contact_fields": asks_contact_fields,
        "is_texas_role": is_texas_role,
    }
