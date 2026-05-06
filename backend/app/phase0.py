import re
from dataclasses import asdict, dataclass

from app.models import UserSettings

SKILL_KEYWORDS = [
    "python",
    "fastapi",
    "sql",
    "postgres",
    "sqlite",
    "react",
    "typescript",
    "java",
    "spring",
    "spring boot",
    "microservices",
    "kafka",
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
EMPLOYER_DOMAINS = {"horizonsofttech.net", "horizonsoftech.net"}


@dataclass
class RoutingEvidence:
    role: str
    email: str
    source: str
    detail: str


@dataclass
class RoutingResult:
    to_email: str | None
    cc_email: str | None
    status: str
    confidence: float
    reason: str
    evidence: list[RoutingEvidence]
    candidates: list[RoutingEvidence]

    def to_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["evidence"] = [asdict(item) for item in self.evidence]
        payload["candidates"] = [asdict(item) for item in self.candidates]
        return payload


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


def extract_email_address(value: str) -> str:
    match = EMAIL_RE.search(value)
    return match.group(0).lower() if match else value.strip().lower()


def email_domain(value: str) -> str:
    email = extract_email_address(value)
    parts = email.split("@", 1)
    return parts[1].lower() if len(parts) == 2 else ""


def _is_employer_email(email: str) -> bool:
    return email_domain(email) in EMPLOYER_DOMAINS


def _is_ignored_email(email: str) -> bool:
    domain = email_domain(email)
    return domain == "googlegroups.com" or email.lower().endswith("+unsubscribe@googlegroups.com")


def _append_unique(items: list[RoutingEvidence], item: RoutingEvidence) -> None:
    key = (item.role, item.email, item.source)
    if key not in {(existing.role, existing.email, existing.source) for existing in items}:
        items.append(item)


def analyze_recipient_routing(
    sender: str,
    subject: str,
    body: str,
    snippet: str = "",
    learned_pairs: list[tuple[str, str]] | None = None,
) -> RoutingResult:
    sender_email = extract_email_address(sender)
    combined_text = f"{subject}\n{body}\n{snippet}"
    evidence: list[RoutingEvidence] = []
    candidates: list[RoutingEvidence] = []

    unique_emails: list[str] = []
    for email in [e.lower() for e in EMAIL_RE.findall(combined_text)]:
        if email not in unique_emails:
            unique_emails.append(email)

    if sender_email and "@" in sender_email and not _is_ignored_email(sender_email):
        role = "cc" if _is_employer_email(sender_email) else "to"
        item = RoutingEvidence(role=role, email=sender_email, source="sender_header", detail="Sender header")
        _append_unique(candidates, item)

    # Prefer recruiter address from forwarded headers: "From: Name <recruiter@domain>"
    forwarded_from_matches = re.findall(r"(?im)^\s*from\s*:\s*.*?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", combined_text)
    forwarded_non_employer = [
        e.lower()
        for e in forwarded_from_matches
        if not _is_employer_email(e.lower()) and not _is_ignored_email(e.lower())
    ]
    for email in forwarded_non_employer:
        _append_unique(
            candidates,
            RoutingEvidence(role="to", email=email, source="forwarded_from", detail="Forwarded From line"),
        )

    for email in unique_emails:
        if _is_ignored_email(email):
            continue
        role = "cc" if _is_employer_email(email) else "to"
        source = "body_employer_contact" if role == "cc" else "body_recruiter_contact"
        _append_unique(candidates, RoutingEvidence(role=role, email=email, source=source, detail="Email body"))

    to_candidates = [item for item in candidates if item.role == "to"]
    cc_candidates = [item for item in candidates if item.role == "cc"]
    selected_to = to_candidates[0] if to_candidates else None
    selected_cc = cc_candidates[0] if cc_candidates else None

    learned_pairs = learned_pairs or []
    text_lower = combined_text.lower()
    for learned_to, learned_cc in learned_pairs:
        learned_to = learned_to.strip().lower()
        learned_cc = learned_cc.strip().lower()
        if learned_to in text_lower and learned_cc in text_lower:
            selected_to = RoutingEvidence(
                role="to",
                email=learned_to,
                source="learned_correction",
                detail="Prior correction matched current email evidence",
            )
            selected_cc = RoutingEvidence(
                role="cc",
                email=learned_cc,
                source="learned_correction",
                detail="Prior correction matched current email evidence",
            )
            break

    if selected_to:
        evidence.append(selected_to)
    if selected_cc:
        evidence.append(selected_cc)

    if selected_to and selected_cc:
        direct_sources = {item.source for item in evidence}
        if "learned_correction" in direct_sources:
            status = "confirmed"
            confidence = 0.92
            reason = "Matched a prior correction and both addresses appear in this email."
        elif selected_to.email != selected_cc.email:
            status = "safe"
            confidence = 0.9
            reason = "Found distinct recruiter and employer contacts in the current email."
        else:
            status = "ambiguous"
            confidence = 0.45
            reason = "To and CC resolved to the same address."
    elif selected_to or selected_cc:
        status = "ambiguous"
        confidence = 0.45
        reason = "Only one recipient side could be resolved."
    else:
        status = "missing"
        confidence = 0.0
        reason = "No usable recruiter or employer routing contacts found."

    return RoutingResult(
        to_email=selected_to.email if selected_to else None,
        cc_email=selected_cc.email if selected_cc else None,
        status=status,
        confidence=confidence,
        reason=reason,
        evidence=evidence,
        candidates=candidates,
    )


def resolve_to_cc(sender: str, subject: str, body: str, snippet: str = "") -> tuple[str | None, str | None]:
    result = analyze_recipient_routing(sender, subject, body, snippet)
    return result.to_email, result.cc_email


def _extract_location(text: str) -> str:
    match = re.search(r"(remote|hybrid|onsite|on-site)", text, re.IGNORECASE)
    return match.group(1).lower() if match else "unknown"


def _extract_salary(text: str) -> str:
    match = re.search(r"(\$?\d{2,3}[,]?\d{0,3}\s?-\s?\$?\d{2,3}[,]?\d{0,3})", text)
    return match.group(1) if match else "not_specified"


def _extract_role(subject: str, body: str) -> str:
    combined = f"{subject}\n{body}"
    labeled_role = re.search(
        r"(?im)^\s*(title|job title|role|position)\s*[:\-]\s*(.+?)\s*$",
        combined,
    )
    if labeled_role:
        role = labeled_role.group(2).strip(" .:-")
        if role:
            return role

    role_patterns = [
        r"(software engineer)",
        r"(backend engineer)",
        r"(frontend engineer)",
        r"(full[- ]stack engineer)",
        r"(full[- ]stack developer)",
        r"(python developer)",
        r"(java(?:\s+\w+){0,4}\s+developer)",
    ]
    for pattern in role_patterns:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            return match.group(1).title()

    cleaned_subject = re.sub(r"(?i)^\s*(re|fw|fwd)\s*:\s*", "", subject).strip()
    if cleaned_subject and cleaned_subject.lower() not in {"no subject", "(no subject)"}:
        return cleaned_subject
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


SKILL_DISPLAY_NAMES = {
    "aws": "AWS",
    "sql": "SQL",
    "postgres": "Postgres",
    "react": "React",
    "spring boot": "Spring Boot",
}

GENERIC_TO_LOCAL_PARTS = {
    "jobs",
    "job",
    "careers",
    "career",
    "hr",
    "hiring",
    "recruiting",
    "recruiter",
    "talent",
    "contact",
    "info",
    "admin",
    "support",
    "hello",
    "team",
    "noreply",
    "no-reply",
}


def _format_skill(skill: str) -> str:
    normalized = skill.strip().lower()
    return SKILL_DISPLAY_NAMES.get(normalized, normalized.title())


def _normalize_person_name(candidate: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z .'-]", " ", candidate).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return None
    parts = cleaned.split()
    if len(parts) < 1 or len(parts) > 4:
        return None
    for part in parts:
        low = part.lower().strip(".")
        if low in {"hi", "hello", "thanks", "regards", "best", "email"}:
            return None
        if not re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", part):
            return None
    return " ".join(part.capitalize() for part in parts)


def _name_from_body_for_email(to_email: str, body: str) -> str | None:
    escaped = re.escape(to_email)
    inline = re.search(rf"(?im)([A-Za-z][A-Za-z .'-]{{1,80}}?)\s*<\s*{escaped}\s*>", body)
    if inline:
        return _normalize_person_name(inline.group(1))

    lines = body.splitlines()
    for idx, line in enumerate(lines):
        if to_email.lower() not in line.lower():
            continue
        before = re.split(re.escape(to_email), line, flags=re.IGNORECASE)[0]
        normalized = _normalize_person_name(before.replace("email", "").replace(":", " ").strip(" -,\t"))
        if normalized:
            return normalized
        for prev_offset in (1, 2):
            prev_idx = idx - prev_offset
            if prev_idx < 0:
                break
            prev_line = lines[prev_idx].strip()
            if not prev_line:
                continue
            normalized_prev = _normalize_person_name(prev_line)
            if normalized_prev:
                return normalized_prev
    return None


def greeting_from_to_contact(to_email: str | None, body: str) -> str:
    _ = to_email
    _ = body
    return "Hi,"


def draft_reply(
    sender: str,
    role: str,
    parsed: dict[str, str | int | bool],
    greeting_line: str = "Hi,",
) -> str:
    include_contact_fields = bool(parsed.get("asks_contact_fields", False))
    matched_skills = str(parsed.get("skills_text", "none_detected"))
    skills = [s.strip() for s in matched_skills.split(",") if s.strip() and s.strip() != "none_detected"]
    skill_summary = [_format_skill(s) for s in skills[:6]] if skills else ["Full-stack development", "Java", "APIs"]
    subject_line = f"Subject: Application for {role} - 7+ Years Full Stack Experience"

    body_lines = [
        subject_line,
        "",
        greeting_line,
        "",
        f"Thank you for sharing the {role} opportunity. I am very interested in this role and excited about the chance to contribute.",
        "I am currently working as a Full Stack Developer at Centier Bank in the banking domain, and I bring 7+ years of experience delivering enterprise applications across backend services and modern web interfaces.",
        "Based on your requirements, my technical alignment includes:",
    ]

    for skill in skill_summary:
        body_lines.append(f"- **{skill}**")

    body_lines.extend(
        [
            "",
            "I have attached my resume for your review and would be glad to discuss how my experience can support your team.",
        ]
    )

    if include_contact_fields:
        body_lines.extend(
            [
                "",
                "Requested details:",
                "- Visa: H1B",
                "- Current Location: Dallas, TX",
            ]
        )

    body_lines.extend(
        [
            "",
            "Best regards,",
            "Chaithanya Dheeraj N",
            "📞 +1 940-629-6920",
            "✉️ chaithanyadheeraj1026@gmail.com",
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

