import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.models import UserSettings
from app.parsing.jd_requirements import (
    REQUIREMENTS_SCHEMA_VERSION,
    parse_structured_jd_requirements,
    requirements_from_payload,
    requirements_to_payload,
    structured_requirements_from_ai_payload,
)
from app.services import policy_service
from app.parsing.ai_extractor import ai_extractor_result_to_payload, extract_ai_job_details
from app.parsing.skill_audit import audit_skills_text, skill_audit_result_to_payload
from app.skill_taxonomy import (
    display_skill_label,
    extract_jd_skills_text,
    extract_skills_text,
    normalize_skills_text,
    score_taxonomy_skills,
)

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
FORWARDED_HEADER_RE = re.compile(r"^\s*(from|sent|to|cc|subject)\s*:", re.IGNORECASE)
FOOTER_SIGNOFF_RE = re.compile(
    r"^\s*(thanks(?:\s*(?:and|&)\s*regards)?|best regards|regards|thanks)\b",
    re.IGNORECASE,
)
FOOTER_TITLE_RE = re.compile(
    r"\b(technical recruiter|recruiter|bench sales recruiter|account manager|talent acquisition|staffing specialist|lead recruiter)\b",
    re.IGNORECASE,
)
PHONE_RE = re.compile(r"(?:\+?\d[\d(). -]{7,}\d)")
URL_RE = re.compile(r"(https?://|www\.)", re.IGNORECASE)
EXPLICIT_INTERVIEW_RE = re.compile(
    r"("
    r"\bonsite[\s,:;-]+interview(?:[\s,:;-]+(?:required|mandatory))?\b|"
    r"\bin[- ]person[\s,:;-]+interview(?:[\s,:;-]+(?:required|mandatory))?\b|"
    r"\blocal[\s,:;-]+onsite[\s,:;-]+interview\b|"
    r"\binterview[\s,:;-]+must[\s,:;-]+be[\s,:;-]+onsite\b|"
    r"\bclient[\s,:;-]+round[\s,:;-]+onsite\b"
    r")",
    re.IGNORECASE,
)
EMPLOYER_DOMAINS = {"horizonsofttech.net", "horizonsoftech.net"}

SECTION_WEIGHTS: dict[str, float] = {
    "mandatory": 1.00,
    "required": 0.95,
    "technical_skills": 0.95,
    "essential": 0.95,
    "responsibilities": 0.80,
    "summary": 0.75,
    "domain": 0.85,
    "ai_compliance": 0.75,
    "preferred": 0.55,
    "hard_filter": 0.00,
    "footer": 0.00,
    "unknown": 0.35,
}

SECTION_HEADING_ALIASES: dict[str, tuple[str, ...]] = {
    "mandatory": (
        "mandatory skills",
        "must have",
        "must-have skills",
        "minimum qualifications",
        "basic qualifications",
    ),
    "required": (
        "required qualifications",
        "required skills",
        "required skills & qualifications",
        "skills required",
        "required experience",
        "qualifications",
    ),
    "technical_skills": (
        "technical skills",
        "tech skill",
        "core skills",
        "primary skills",
        "knowledge and skills",
    ),
    "essential": (
        "essential skills",
        "hands-on experience",
    ),
    "responsibilities": (
        "key responsibilities",
        "responsibilities",
        "role responsibilities",
        "job responsibilities",
        "duties",
        "what you will do",
        "what you'll do",
        "day-to-day responsibilities",
    ),
    "summary": (
        "job description",
        "role description",
        "role descriptions",
        "job summary",
        "role summary",
        "overview",
        "project overview",
        "about the role",
    ),
    "preferred": (
        "preferred qualifications",
        "preferred skills",
        "nice to have",
        "good to have",
        "plus",
        "bonus skills",
        "desired skills",
        "additional skills",
        "additional notes",
        "exposure to",
        "familiarity with",
    ),
    "domain": (
        "domain skill",
        "domain skills",
        "domain experience",
        "industry experience",
        "banking domain",
        "healthcare domain",
        "client environment",
        "project context",
    ),
    "ai_compliance": (
        "compliance & responsible ai expectations",
        "responsible ai expectations",
        "compliance expectations",
        "security requirements",
        "data privacy",
        "secure sdlc",
        "guardrails",
        "evaluation requirements",
        "what success looks like",
        "operational readiness",
    ),
    "hard_filter": (
        "location",
        "job location",
        "work location",
        "visa",
        "duration",
        "rate",
        "interview mode",
        "local only",
        "onsite",
        "hybrid",
        "remote",
        "experience",
        "years of experience",
        "education",
        "client",
    ),
    "footer": (
        "regards",
        "best regards",
        "thanks",
        "thanks & regards",
        "contact",
    ),
}

SECTION_HEADING_LOOKUP = {
    alias: bucket
    for bucket, aliases in SECTION_HEADING_ALIASES.items()
    for alias in aliases
}

SYNTHETIC_REQUIREMENT_PREFIXES: tuple[tuple[str, str], ...] = (
    ("must have", "mandatory"),
    ("required", "required"),
    ("required skills", "required"),
    ("required qualifications", "required"),
    ("strong proficiency in", "technical_skills"),
    ("proficiency in", "technical_skills"),
    ("experience with", "essential"),
    ("hands-on experience", "essential"),
    ("knowledge of", "technical_skills"),
)


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


@dataclass
class JDSection:
    heading: str
    bucket: str
    weight: float
    text: str
    start_line: int | None = None
    end_line: int | None = None


def is_recruiter_like(sender: str, subject: str, body: str) -> bool:
    combined = f"{sender} {subject} {body}".lower()
    return any(hint in combined for hint in RECRUITER_HINTS)


def extract_email_address(value: str) -> str:
    match = EMAIL_RE.search(value)
    return match.group(0).lower() if match else value.strip().lower()


def email_domain(value: str) -> str:
    email = extract_email_address(value)
    parts = email.split("@", 1)
    return parts[1].lower() if len(parts) == 2 else ""


def normalize_employer_domains(raw_domains: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    if not raw_domains:
        return set(EMPLOYER_DOMAINS)
    normalized = {str(domain).strip().lower() for domain in raw_domains if str(domain).strip()}
    return normalized or set(EMPLOYER_DOMAINS)


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
    employer_domains: list[str] | tuple[str, ...] | set[str] | None = None,
) -> RoutingResult:
    effective_employer_domains = normalize_employer_domains(employer_domains)
    def is_employer_email(email: str) -> bool:
        return email_domain(email) in effective_employer_domains

    sender_email = extract_email_address(sender)
    combined_text = f"{subject}\n{body}\n{snippet}"
    evidence: list[RoutingEvidence] = []
    candidates: list[RoutingEvidence] = []

    unique_emails: list[str] = []
    for email in [e.lower() for e in EMAIL_RE.findall(combined_text)]:
        if email not in unique_emails:
            unique_emails.append(email)

    if sender_email and "@" in sender_email and not _is_ignored_email(sender_email):
        role = "cc" if is_employer_email(sender_email) else "to"
        item = RoutingEvidence(role=role, email=sender_email, source="sender_header", detail="Sender header")
        _append_unique(candidates, item)

    # Prefer recruiter address from forwarded headers: "From: Name <recruiter@domain>"
    forwarded_from_matches = re.findall(r"(?im)^\s*from\s*:\s*.*?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", combined_text)
    forwarded_non_employer = [
        e.lower()
        for e in forwarded_from_matches
        if not is_employer_email(e.lower()) and not _is_ignored_email(e.lower())
    ]
    for email in forwarded_non_employer:
        _append_unique(
            candidates,
            RoutingEvidence(role="to", email=email, source="forwarded_from", detail="Forwarded From line"),
        )

    for email in unique_emails:
        if _is_ignored_email(email):
            continue
        role = "cc" if is_employer_email(email) else "to"
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
    if match:
        return match.group(1).lower()
    labeled = re.search(
        r"(?is)\b(?:location|job location)\s*[:\-]\s*([A-Za-z .'-]+,\s*[A-Z]{2})(?=\s+\b(?:duration|visa|rate|client|job id|summary|responsibilities|required qualifications|preferred qualifications|what success looks like|compliance)\b\s*[:\-]|\n|$)",
        text,
    )
    if labeled:
        return labeled.group(1).strip()
    city_state = re.search(r"\b([A-Za-z .'-]+,\s*[A-Z]{2})\b", text)
    if city_state:
        return city_state.group(1).strip()
    return "unknown"


def _extract_salary(text: str) -> str:
    match = re.search(r"(\$?\d{2,3}[,]?\d{0,3}\s?-\s?\$?\d{2,3}[,]?\d{0,3})", text)
    return match.group(1) if match else "not_specified"


def _extract_role(subject: str, body: str) -> str:
    combined = f"{subject}\n{body}"
    labeled_role = re.search(
        r"(?is)\b(?:title|job title|role|position)\s*[:\-]\s*(.+?)(?=\s+\b(?:location|duration|visa|rate|client|job id|summary|responsibilities|required qualifications|preferred qualifications|what success looks like|compliance)\b\s*[:\-]|\n|$)",
        combined,
    )
    if labeled_role:
        role = labeled_role.group(1).strip(" .:-")
        if role:
            return role

    role_patterns = [
        r"(applied ai engineer)",
        r"(genai engineer)",
        r"(llm engineer)",
        r"(ml engineer)",
        r"(machine learning engineer)",
        r"(prompt engineer)",
        r"(ai engineer(?:\s+\w+)?)",
        r"(ai developer)",
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
    return extract_skills_text(text)


def _normalize_section_heading(value: str) -> str:
    heading = re.sub(r"[\s_]+", " ", str(value or "").strip().lower())
    heading = re.sub(r"\s*&\s*", " & ", heading)
    return re.sub(r"\s+", " ", heading).strip(" :-")


def classify_section_heading(heading: str) -> tuple[str, float]:
    normalized = _normalize_section_heading(heading)
    bucket = SECTION_HEADING_LOOKUP.get(normalized, "unknown")
    return bucket, SECTION_WEIGHTS.get(bucket, SECTION_WEIGHTS["unknown"])


def _split_heading_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None

    exact_bucket, _ = classify_section_heading(stripped)
    if exact_bucket != "unknown":
        return stripped, ""

    if ":" not in stripped:
        return None

    heading_part, remainder = stripped.split(":", 1)
    bucket, _ = classify_section_heading(heading_part)
    if bucket == "unknown":
        return None
    return heading_part.strip(), remainder.strip()


def _build_section(
    heading: str,
    bucket: str,
    text_lines: list[str],
    start_line: int | None,
    end_line: int | None,
) -> JDSection:
    text = "\n".join(line.rstrip() for line in text_lines).strip()
    return JDSection(
        heading=heading.strip(),
        bucket=bucket,
        weight=SECTION_WEIGHTS.get(bucket, SECTION_WEIGHTS["unknown"]),
        text=text,
        start_line=start_line,
        end_line=end_line,
    )


def _looks_like_synthetic_requirement(line: str) -> tuple[str, float] | None:
    normalized = _normalize_section_heading(line)
    for prefix, bucket in SYNTHETIC_REQUIREMENT_PREFIXES:
        if normalized.startswith(prefix):
            return bucket, SECTION_WEIGHTS.get(bucket, SECTION_WEIGHTS["unknown"])
    return None


def slice_jd_sections(body: str) -> list[JDSection]:
    cleaned = strip_recruiter_footer(strip_forward_headers(body or ""))
    lines = cleaned.splitlines()
    sections: list[JDSection] = []
    current_heading = "Body"
    current_bucket = "unknown"
    current_start: int | None = None
    current_lines: list[str] = []
    found_heading = False

    def flush(end_idx: int) -> None:
        nonlocal current_heading, current_bucket, current_start, current_lines
        if current_start is None:
            return
        if not any(line.strip() for line in current_lines):
            current_heading = "Body"
            current_bucket = "unknown"
            current_start = None
            current_lines = []
            return
        sections.append(
            _build_section(
                heading=current_heading,
                bucket=current_bucket,
                text_lines=current_lines,
                start_line=current_start,
                end_line=end_idx,
            )
        )
        current_heading = "Body"
        current_bucket = "unknown"
        current_start = None
        current_lines = []

    for idx, raw_line in enumerate(lines):
        line = raw_line.rstrip()
        split_heading = _split_heading_line(line)
        if split_heading:
            flush(idx - 1)
            heading, remainder = split_heading
            current_heading = heading
            current_bucket, _ = classify_section_heading(heading)
            current_start = idx
            current_lines = [remainder] if remainder else []
            found_heading = True
            continue

        if current_start is None:
            current_start = idx
            current_heading = "Body"
            current_bucket = "unknown"
        current_lines.append(line)

    flush(len(lines) - 1)

    synthetic_sections: list[JDSection] = []
    covered_lines = {
        line_no
        for section in sections
        if section.bucket != "unknown"
        for line_no in range(section.start_line or 0, (section.end_line or -1) + 1)
    }
    for idx, raw_line in enumerate(lines):
        if idx in covered_lines:
            continue
        stripped = raw_line.strip()
        if not stripped:
            continue
        synthetic = _looks_like_synthetic_requirement(stripped)
        if not synthetic:
            continue
        bucket, weight = synthetic
        synthetic_sections.append(
            JDSection(
                heading="Synthetic Requirement",
                bucket=bucket,
                weight=weight,
                text=stripped,
                start_line=idx,
                end_line=idx,
            )
        )

    if not found_heading and synthetic_sections:
        return synthetic_sections

    if not sections and cleaned.strip():
        sections.append(
            JDSection(
                heading="Body",
                bucket="unknown",
                weight=SECTION_WEIGHTS["unknown"],
                text=cleaned.strip(),
                start_line=0,
                end_line=max(len(lines) - 1, 0),
            )
        )

    sections.extend(synthetic_sections)
    sections.sort(key=lambda section: (section.start_line or 0, section.end_line or 0, section.heading))
    return sections


def build_skill_source_sections(sections: list[JDSection]) -> list[JDSection]:
    eligible = [
        section
        for section in sections
        if section.bucket not in {"hard_filter", "footer"} and section.text.strip()
    ]
    if eligible:
        return eligible

    fallback_text = "\n".join(section.text for section in sections if section.text.strip()).strip()
    if not fallback_text:
        return []

    return [
        JDSection(
            heading="Body",
            bucket="unknown",
            weight=SECTION_WEIGHTS["unknown"],
            text=fallback_text,
            start_line=sections[0].start_line if sections else 0,
            end_line=sections[-1].end_line if sections else 0,
        )
    ]


def strip_forward_headers(body: str) -> str:
    lines = body.splitlines()
    if not lines:
        return body

    header_lines = 0
    idx = 0
    while idx < len(lines):
        line = lines[idx].strip()
        if not line:
            if header_lines >= 2:
                return "\n".join(lines[idx + 1 :]).strip()
            break
        if FORWARDED_HEADER_RE.match(line):
            header_lines += 1
            idx += 1
            continue
        break
    return body


def _looks_like_footer_cluster(lines: list[str], start_idx: int) -> bool:
    window = [line.strip() for line in lines[start_idx : start_idx + 6] if line.strip()]
    if not window:
        return False
    title_hits = sum(1 for line in window if FOOTER_TITLE_RE.search(line))
    contact_hits = sum(
        1
        for line in window
        if EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line)
    )
    return title_hits >= 1 and contact_hits >= 1


def strip_recruiter_footer(body: str) -> str:
    lines = body.splitlines()
    if not lines:
        return body

    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if not line:
            continue
        normalized = re.sub(r"\s+", " ", line).strip().lower()
        if FOOTER_SIGNOFF_RE.match(line) and _looks_like_footer_cluster(lines, idx):
            return "\n".join(lines[:idx]).strip()
        if FOOTER_TITLE_RE.search(line) and _looks_like_footer_cluster(lines, idx):
            return "\n".join(lines[:idx]).strip()
        near_message_end = idx >= max(0, len(lines) - 8)
        if "unsubscribe" in normalized and near_message_end:
            return "\n".join(lines[:idx]).strip()
        if (
            near_message_end
            and any(phrase in normalized for phrase in ("reply if interested", "please share resume", "call me"))
            and _looks_like_footer_cluster(lines, idx)
        ):
            return "\n".join(lines[:idx]).strip()

    inline_footer_patterns = [
        r"(?is)\b(?:thanks(?:\s*(?:and|&)\s*regards)?|best regards|regards)\b.*$",
        r"(?is)\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){0,3}\s+(?:technical recruiter|recruiter|account manager)\b(?=.*(?:email\s*:|phone\s*:|ph\s*:|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})).*$",
    ]
    for pattern in inline_footer_patterns:
        without_footer = re.sub(pattern, "", body)
        stripped = without_footer.strip()
        if stripped and without_footer != body and len(stripped) >= max(80, int(len(body) * 0.35)):
            return stripped
    return body


def _extract_location_text(subject: str, body: str) -> str:
    combined = f"{subject}\n{body}"
    line_hit = re.search(
        r"(?is)\b(?:location|job location)\s*[:\-]\s*(.+?)(?=\s+\b(?:duration|visa|rate|client|job id|summary|responsibilities|required qualifications|preferred qualifications|what success looks like|compliance)\b\s*[:\-]|\n|$)",
        combined,
    )
    if line_hit:
        return line_hit.group(1).strip()

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


def hard_filter_check(
    parsed: dict[str, str | int | bool],
    settings: UserSettings,
    policy: policy_service.PolicyConfig | None = None,
    parser_details: Mapping[str, Any] | None = None,
) -> tuple[bool, str]:
    blocked_reasons: list[str] = []
    warning_reasons: list[str] = []
    rules = policy_service.draft_rules(policy)
    structured_requirements = requirements_from_payload(
        ((parser_details or {}).get("structured_requirements") if isinstance(parser_details, Mapping) else None)
    )

    def _normalize_requirement_names(values: list[str]) -> set[str]:
        normalized: set[str] = set()
        for value in values:
            skill = str(value or "").strip()
            if not skill:
                continue
            normalized.add(skill.lower())
            normalized.add(display_skill_label(skill).lower())
        return {value for value in normalized if value}

    def _structured_skill_inventory() -> set[str]:
        skills: set[str] = set()
        for group in (
            *structured_requirements.required_groups,
            *structured_requirements.preferred_groups,
            *structured_requirements.informational_groups,
        ):
            for skill in group.skills:
                skills.update(_normalize_requirement_names([skill.canonical_name, skill.matched_alias or ""]))
        return skills

    def _effective_locations() -> set[str]:
        values = {str(parsed.get("location") or "").strip().lower()}
        for location in structured_requirements.locations:
            cleaned = str(location).strip().lower()
            if not cleaned:
                continue
            values.add(cleaned)
            if "tx" in cleaned or "texas" in cleaned:
                values.add("texas")
            if "remote" in cleaned:
                values.add("remote")
        work_mode = str(structured_requirements.work_mode or "").strip().lower()
        if work_mode:
            values.add(work_mode)
        if structured_requirements.local_required:
            values.add("local")
            values.add("local only")
        return {value for value in values if value}

    accepted_rule = rules["accepted_location"]
    accepted_mode = policy_service.draft_rule_mode(policy, "accepted_location")
    accepted_from_rule = [loc.strip().lower() for loc in accepted_rule.get("locations", []) if loc.strip()]
    accepted = accepted_from_rule or [loc.strip().lower() for loc in settings.accepted_locations.split(",") if loc.strip()]
    if accepted_mode != "ignore" and accepted:
        effective_locations = _effective_locations()
        if accepted and "any" not in accepted and not any(location in accepted for location in effective_locations):
            if accepted_mode == "block":
                blocked_reasons.append("location_mismatch")
            else:
                warning_reasons.append("location_mismatch")

    minimum_salary_rule = rules["minimum_salary"]
    minimum_salary_mode = policy_service.draft_rule_mode(policy, "minimum_salary")
    minimum_salary = minimum_salary_rule.get("value")
    if minimum_salary is None:
        minimum_salary = settings.min_salary
    if minimum_salary_mode != "ignore" and minimum_salary is not None:
        salary_floor = _parse_salary_floor(str(parsed["salary_text"]))
        if salary_floor is not None and salary_floor < minimum_salary:
            if minimum_salary_mode == "block":
                blocked_reasons.append("salary_below_min")
            else:
                warning_reasons.append("salary_below_min")

    must_have_rule = rules["must_have_skills"]
    must_have_mode = policy_service.draft_rule_mode(policy, "must_have_skills")
    if must_have_mode != "ignore":
        combined = f"{parsed['role']} {parsed['skills_text']}".lower()
        must_have_from_rule = [s.strip().lower() for s in must_have_rule.get("skills", []) if s.strip()]
        must_have_skills = must_have_from_rule or [s.strip().lower() for s in settings.must_have_skills.split(",") if s.strip()]
        structured_skills = _structured_skill_inventory()
        missing: list[str] = []
        for skill in must_have_skills:
            normalized_targets = _normalize_requirement_names([skill])
            if structured_skills:
                if not normalized_targets.intersection(structured_skills):
                    missing.append(skill)
                continue
            if skill not in combined:
                missing.append(skill)
        if missing:
            reason = f"missing_skills:{'|'.join(missing)}"
            if must_have_mode == "block":
                blocked_reasons.append(reason)
            else:
                warning_reasons.append(reason)

    if blocked_reasons:
        return False, f"blocked: {', '.join(blocked_reasons)}"
    if warning_reasons:
        return True, policy_service.combine_rule_messages(warning_reasons)
    return True, "hard_filters_passed"


def ai_assist_score(parsed: dict[str, str | int], settings: UserSettings) -> tuple[float, str]:
    text = f"{parsed['role']} {parsed['skills_text']} {settings.free_text_guidance}".lower()
    score = 0.45

    role_keywords = [k.strip().lower() for k in settings.role_keywords.split(",") if k.strip()]
    if role_keywords:
        hits = sum(1 for k in role_keywords if k in text)
        score += min(hits * 0.08, 0.24)

    score += score_taxonomy_skills(str(parsed.get("skills_text", "")))
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

DEFAULT_SIGNATURE_NAME = "Chaithanya Dheeraj N"
DEFAULT_SIGNATURE_PHONE = "+1 940-629-6920"
DEFAULT_SIGNATURE_EMAIL = "chaithanyadheeraj1026@gmail.com"
DEFAULT_GREETING_LINE = "Dear Recruiter,"

DEFAULT_FALLBACK_DRAFT_TEMPLATE = """Subject: Application for {{role}} - 7+ Years Full Stack Experience

{{greeting}}

Thank you for sharing the {{role}} opportunity. I am very interested in this role and excited about the chance to contribute.
I am currently working as a Full Stack Developer at Centier Bank in the banking domain, and I bring 7+ years of experience delivering enterprise applications across backend services and modern web interfaces.
Based on your requirements, my technical alignment includes:
{{skills_list}}

I have attached my resume for your review and would be glad to discuss how my experience can support your team.
{{requested_details_block}}

Best regards,
{{signature_name}}
📞 {{signature_phone}}
✉️ {{signature_email}}"""


def _format_skill(skill: str) -> str:
    label = display_skill_label(skill)
    return label or str(skill or "").strip().title()


def skills_from_text(skills_text: str) -> list[str]:
    skills = [s.strip() for s in str(skills_text).split(",") if s.strip() and s.strip() != "none_detected"]
    return [_format_skill(s) for s in skills[:6]] if skills else ["Full-stack development", "Java", "APIs"]


def requested_details_block(asks_contact_fields: bool) -> str:
    if not asks_contact_fields:
        return ""
    return "Requested details:\n- Visa: H1B\n- Current Location: Dallas, TX"


def render_fallback_draft_template(template: str, context: dict[str, str]) -> str:
    rendered = template or ""
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", value)
    rendered = re.sub(r"\n{3,}", "\n\n", rendered).strip()
    return rendered


def greeting_from_to_contact(to_email: str | None, body: str) -> str:
    _ = (to_email, body)
    return DEFAULT_GREETING_LINE


def draft_reply(
    sender: str,
    role: str,
    parsed: dict[str, str | int | bool],
    greeting_line: str = DEFAULT_GREETING_LINE,
) -> str:
    include_contact_fields = bool(parsed.get("asks_contact_fields", False))
    skill_summary = skills_from_text(str(parsed.get("skills_text", "none_detected")))
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
                *requested_details_block(True).split("\n"),
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

def _base_parse_email(subject: str, body: str) -> dict[str, str | int | bool]:
    role = _extract_role(subject, body)
    location = _extract_location(body)
    salary_text = _extract_salary(body)
    cleaned_body = strip_recruiter_footer(strip_forward_headers(body))
    sections = slice_jd_sections(cleaned_body)
    skill_sections = build_skill_source_sections(sections)
    fallback_text = f"{subject} {cleaned_body}".strip() if cleaned_body else f"{subject} {body}".strip()
    has_structured_skill_sections = any(
        section.bucket not in {"unknown", "hard_filter", "footer"} for section in sections
    )
    if has_structured_skill_sections and skill_sections:
        skills_text = extract_jd_skills_text(
            skill_sections,
            role_text=role,
            fallback_text=fallback_text,
        )
    else:
        skills_text = _extract_skills(fallback_text)
    location_text = _extract_location_text(subject, body)
    combined_text = f"{subject}\n{body}"
    f2f_mentioned = bool(F2F_RE.search(combined_text) or EXPLICIT_INTERVIEW_RE.search(combined_text))
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


def _should_run_ai_extractor(
    subject: str,
    body: str,
    *,
    source: str,
    ai_extractor_enabled: bool,
) -> bool:
    if not ai_extractor_enabled:
        return False
    if source not in {"gmail", "manual", "nvoids"}:
        return False
    combined_text = " ".join(part.strip() for part in [subject, body] if part and str(part).strip()).strip()
    return len(combined_text) >= 80 or len(str(body or "").strip()) >= 60


def _apply_nvoids_source_hints(
    merged: dict[str, str | int | bool],
    *,
    source: str,
    source_hints: Mapping[str, Any] | None,
) -> None:
    if source != "nvoids":
        return
    hints = dict(source_hints or {})
    canonical_title = str(hints.get("canonical_title") or "").strip()
    canonical_location = str(hints.get("canonical_location") or "").strip()
    if canonical_title:
        merged["role"] = canonical_title
    if canonical_location:
        merged["location"] = canonical_location
        merged["job_location_text"] = canonical_location


def _empty_contract_defaults() -> dict[str, str | int | bool]:
    return {
        "role": "Unknown Role",
        "location": "unknown",
        "job_location_text": "unknown",
        "salary_text": "not_specified",
        "skills_text": "none_detected",
        "f2f_mentioned": False,
        "asks_contact_fields": False,
        "is_texas_role": False,
    }


def _ai_primary_parse_result(
    ai_payload: Mapping[str, Any] | None,
    *,
    source: str,
    source_hints: Mapping[str, Any] | None,
) -> dict[str, str | int | bool]:
    parsed: dict[str, str | int | bool] = _empty_contract_defaults()
    payload = dict(ai_payload or {})
    role_candidates = payload.get("role_candidates")
    if isinstance(role_candidates, list):
        for candidate in role_candidates:
            role_text = str(candidate or "").strip()
            if role_text:
                parsed["role"] = role_text
                break

    primary_location = str(payload.get("primary_location") or "").strip()
    if primary_location:
        parsed["location"] = primary_location
        parsed["job_location_text"] = primary_location

    salary_text = str(payload.get("salary_text") or "").strip()
    if salary_text:
        parsed["salary_text"] = salary_text

    skills_text = normalize_skills_text(str(payload.get("skills_text") or ""), preserve_unknown=True)
    if skills_text and skills_text != "none_detected":
        parsed["skills_text"] = skills_text

    parsed["f2f_mentioned"] = bool(payload.get("f2f_mentioned", False))
    parsed["asks_contact_fields"] = bool(payload.get("asks_contact_fields", False))
    parsed["is_texas_role"] = bool(payload.get("is_texas_role", False))

    _apply_nvoids_source_hints(parsed, source=source, source_hints=source_hints)
    location_text = str(parsed.get("job_location_text") or parsed.get("location") or "").strip()
    parsed["is_texas_role"] = bool(TEXAS_RE.search(location_text))
    return parsed


def parse_email_with_details(
    subject: str,
    body: str,
    *,
    source: str = "gmail",
    source_hints: Mapping[str, Any] | None = None,
    ai_extractor_enabled: bool = False,
    ai_body_override: str | None = None,
) -> tuple[dict[str, str | int | bool], dict[str, Any]]:
    base = _base_parse_email(subject, body)
    base["skills_text"] = audit_skills_text(
        normalize_skills_text(str(base.get("skills_text", "")), preserve_unknown=True)
    ).skills_text
    _apply_nvoids_source_hints(base, source=source, source_hints=source_hints)
    section_body = ai_body_override if source == "nvoids" and ai_body_override is not None else body
    cleaned_body = strip_recruiter_footer(strip_forward_headers(section_body or ""))
    sections = slice_jd_sections(cleaned_body)
    rules_requirements = parse_structured_jd_requirements(
        build_skill_source_sections(sections),
        full_text="\n".join(part for part in [subject, cleaned_body] if str(part or "").strip()),
        source_hints=source_hints,
    )

    final_parsed: dict[str, str | int | bool] = dict(base)
    ai_merge_notes: list[str] = []
    ai_payload: dict[str, Any] | None = None
    structured_requirements_payload = requirements_to_payload(rules_requirements)
    parser_mode = "base_only"
    parser_warning: str | None = None
    fallback_used = False
    ai_body = ai_body_override if ai_body_override is not None else body

    if _should_run_ai_extractor(
        subject,
        ai_body,
        source=source,
        ai_extractor_enabled=ai_extractor_enabled,
    ):
        try:
            ai_result = extract_ai_job_details(
                subject,
                ai_body,
                source=source,
                source_hints=dict(source_hints or {}),
            )
            if ai_result.error:
                raise RuntimeError(ai_result.error)
        except Exception as exc:
            fallback_used = True
            parser_mode = "ai_fallback"
            parser_warning = f"AI extractor failed; base parser fallback used: {exc}"
            ai_payload = {"error": str(exc), "evidence": {"extractor_error": [str(exc)]}}
            ai_merge_notes.append(parser_warning)
        else:
            ai_payload = ai_extractor_result_to_payload(ai_result)
            final_parsed = _ai_primary_parse_result(
                ai_payload,
                source=source,
                source_hints=source_hints,
            )
            structured_requirements_payload = requirements_to_payload(
                structured_requirements_from_ai_payload(ai_payload, fallback=rules_requirements)
            )
            parser_mode = "ai_primary"

    final_skills_audit = audit_skills_text(str(final_parsed.get("skills_text", "")))
    final_parsed["skills_text"] = final_skills_audit.skills_text
    parser_details = {
        "parser_version": {
            "base_only": "base_only_v2",
            "ai_primary": "ai_primary_v2",
            "ai_fallback": "ai_fallback_v2",
        }[parser_mode],
        "parser_mode": parser_mode,
        "source": source,
        "base_parser_result": base,
        "ai_extractor_result": ai_payload,
        "approved_skills_text": ", ".join(final_skills_audit.known) if final_skills_audit.known else "none_detected",
        "unknown_skills": list(final_skills_audit.unknown),
        "skills_audit": skill_audit_result_to_payload(final_skills_audit),
        "merged_result": final_parsed,
        "ai_merge_notes": ai_merge_notes,
        "parser_warning": parser_warning,
        "fallback_used": fallback_used,
        "source_hints": {key: value for key, value in dict(source_hints or {}).items() if value not in (None, "")},
        "ai_input_source": str((source_hints or {}).get("ai_input_source") or ""),
        "ai_input_chars": int((source_hints or {}).get("ai_input_chars") or 0),
        "structured_requirements": structured_requirements_payload,
        "requirements_schema_version": REQUIREMENTS_SCHEMA_VERSION,
    }
    return final_parsed, parser_details


def parse_email(subject: str, body: str) -> dict[str, str | int | bool]:
    merged, _details = parse_email_with_details(subject, body)
    return merged

