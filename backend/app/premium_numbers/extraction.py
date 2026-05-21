from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings
from app.premium_numbers.phone_normalization import format_phone
from app.premium_numbers.prompting import build_premium_numbers_prompts

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s().]{7,}\d)")
JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
DESIGNATION_RE = re.compile(
    r"\b(recruiter|bench sales recruiter|talent acquisition|hiring manager|account manager|vendor)\b",
    re.IGNORECASE,
)
NAME_LINE_RE = re.compile(r"^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})\s*$")
EMAIL_LOCAL_NAME_RE = re.compile(r"\b([A-Za-z][A-Za-z.'\-]{1,30})@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
TARGET_CONTACT_EMAIL_RE = re.compile(
    r"\bto\b[\s\S]{0,120}?([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})",
    re.IGNORECASE,
)
GENERIC_LOCAL_NAME_TOKENS = {
    "admin",
    "careers",
    "contact",
    "hello",
    "hr",
    "info",
    "jobs",
    "mail",
    "noreply",
    "recruiter",
    "support",
    "team",
}


@dataclass(frozen=True)
class ExtractedPhoneLead:
    phone_number_display: str
    phone_number_normalized: str
    owner_name: str
    contact_email: str
    company: str
    designation: str
    purpose: str
    confidence: str
    contact_type: str
    recruiter_relevance_score: int
    is_recruiter_relevant: bool
    relevance_reason: str
    source_fragment: str


def _normalize_phone(raw: str) -> str:
    normalized, _display, _ext = format_phone(raw)
    return normalized


def _display_phone(raw: str) -> str:
    _normalized, display, _ext = format_phone(raw)
    return display or re.sub(r"\s+", " ", raw.strip())


def _normalize_confidence(raw: str) -> str:
    value = (raw or "").strip().lower()
    if value == "high":
        return "high"
    if value == "medium":
        return "medium"
    return "low"


def _safe_json_parse(text: str) -> dict[str, object] | None:
    content = (text or "").strip()
    if not content:
        return None
    try:
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = JSON_BLOCK_RE.search(content)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None


def _llm_extract(email_content: str) -> list[ExtractedPhoneLead]:
    if not settings.deepseek_api_key:
        return []
    system_prompt, user_prompt = build_premium_numbers_prompts(email_content)
    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_seconds,
    )
    response = client.chat.completions.create(
        model=settings.deepseek_model_fast or "deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        max_tokens=700,
    )
    content = response.choices[0].message.content if response.choices else ""
    payload = _safe_json_parse(content or "")
    if not payload:
        return []
    items = payload.get("phone_numbers")
    if not isinstance(items, list):
        return []

    leads: list[ExtractedPhoneLead] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        display = _display_phone(str(item.get("phone_number", "")))
        normalized = _normalize_phone(display)
        if not normalized:
            continue
        leads.append(
            ExtractedPhoneLead(
                phone_number_display=display,
                phone_number_normalized=normalized,
                owner_name=str(item.get("owner_name", "Unknown")).strip() or "Unknown",
                contact_email=str(item.get("email", "")).strip().lower(),
                company=str(item.get("company", "Unknown")).strip() or "Unknown",
                designation=str(item.get("designation", "Unknown")).strip() or "Unknown",
                purpose=str(item.get("purpose", "Recruiter contact")).strip() or "Recruiter contact",
                confidence=_normalize_confidence(str(item.get("confidence", "low"))),
                contact_type="unknown",
                recruiter_relevance_score=0,
                is_recruiter_relevant=False,
                relevance_reason="llm_only_unclassified",
                source_fragment="Extracted by AI from email context",
            )
        )
    return leads


def _sender_name(sender: str) -> str:
    if "<" in sender:
        return sender.split("<", 1)[0].strip().strip('"') or "Unknown"
    return "Unknown"


def _sender_company(sender: str) -> str:
    if "@" not in sender:
        return "Unknown"
    domain = sender.split("@", 1)[1].strip("> ").lower()
    base = domain.split(".", 1)[0].strip()
    return base.title() if base else "Unknown"


def _sender_email(sender: str) -> str:
    text = (sender or "").strip()
    if "<" in text and ">" in text:
        text = text.split("<", 1)[1].split(">", 1)[0].strip()
    match = EMAIL_RE.search(text)
    return (match.group(0).lower() if match else "")


def _domain_from_email(value: str) -> str:
    text = (value or "").strip().lower()
    if "<" in text and ">" in text:
        text = text.split("<", 1)[1].split(">", 1)[0].strip()
    if "@" not in text:
        return ""
    return text.split("@", 1)[1].strip()


def _extract_contact_email(fragment: str, sender: str) -> str:
    matches = EMAIL_RE.findall(fragment or "")
    if matches:
        return matches[-1].lower()
    return _sender_email(sender)


def _extract_owner_name(fragment: str, sender: str) -> str:
    # Prefer explicit target-contact contexts (e.g. "share resume to rabbanis@...").
    contact_match = TARGET_CONTACT_EMAIL_RE.search(fragment or "")
    if contact_match:
        target_email = contact_match.group(1).strip()
        local_match = EMAIL_LOCAL_NAME_RE.search(target_email)
        if local_match:
            local = re.sub(r"[^A-Za-z]", "", local_match.group(1) or "").strip()
            if len(local) >= 2 and local.lower() not in GENERIC_LOCAL_NAME_TOKENS:
                return local.title()

    lines = [line.strip(" -,\t\r") for line in (fragment or "").splitlines() if line.strip()]
    for line in lines:
        if EMAIL_RE.search(line):
            continue
        if DESIGNATION_RE.search(line):
            continue
        if any(ch.isdigit() for ch in line):
            continue
        match = NAME_LINE_RE.match(line)
        if match:
            return match.group(1).strip()

    # Fallback: infer owner name from contact-style email mention in the fragment
    # e.g. "please share ... to Rabbanis@kgatetech.com - +1 832-271-3861"
    for line in lines:
        if "@" not in line:
            continue
        match = EMAIL_LOCAL_NAME_RE.search(line)
        if not match:
            continue
        local = re.sub(r"[^A-Za-z]", "", match.group(1) or "").strip()
        if len(local) < 2:
            continue
        if local.lower() in GENERIC_LOCAL_NAME_TOKENS:
            continue
        return local.title()
    return _sender_name(sender)


def _lead_quality_score(lead: ExtractedPhoneLead) -> int:
    score = 0
    owner = (lead.owner_name or "").strip().lower()
    if owner and owner != "unknown":
        score += 2
    if (lead.contact_email or "").strip():
        score += 1
    if (lead.designation or "").strip().lower() != "unknown":
        score += 1
    if lead.is_recruiter_relevant:
        score += 1
    return score


def _classify_recruiter_relevance(
    *,
    sender: str,
    purpose: str,
    designation: str,
    source_fragment: str,
    employer_domains: set[str],
) -> tuple[str, int, bool, str]:
    score = 50
    reasons: list[str] = []
    domain = _domain_from_email(sender)
    purpose_l = (purpose or "").lower()
    designation_l = (designation or "").lower()
    fragment_l = (source_fragment or "").lower()

    positive_terms = ("direct contact", "job requirements", "submission", "call me", "reach me", "interview")
    negative_terms = ("office", "front desk", "vendor desk", "signature block")
    cta_terms = (" call ", " text ", " reach ", " available")

    if domain and domain not in employer_domains:
        score += 25
        reasons.append("external_domain")
    elif domain and domain in employer_domains:
        score -= 45
        reasons.append("employer_domain")

    if any(term in purpose_l for term in positive_terms):
        score += 20
        reasons.append("purpose_positive")
    if any(term in purpose_l for term in negative_terms):
        score -= 25
        reasons.append("purpose_negative")

    if "recruiter" in designation_l or "talent" in designation_l or "engagement" in designation_l:
        score += 10
        reasons.append("recruiter_designation")
    if "vendor" in designation_l:
        score -= 5
        reasons.append("vendor_designation")

    if any(term in f" {fragment_l} " for term in cta_terms):
        score += 10
        reasons.append("cta_context")

    score = max(0, min(score, 100))
    if score >= 70:
        contact_type = "recruiter_direct"
        is_recruiter_relevant = True
    elif score >= 55 and "submission" in purpose_l:
        contact_type = "submission_contact"
        is_recruiter_relevant = True
    elif domain and domain in employer_domains and score >= 35:
        contact_type = "ambiguous_employer_domain"
        is_recruiter_relevant = False
    elif domain and domain in employer_domains:
        contact_type = "employer_internal"
        is_recruiter_relevant = False
    else:
        contact_type = "unknown"
        is_recruiter_relevant = False
    reason = ",".join(reasons) if reasons else "insufficient_signals"
    return contact_type, score, is_recruiter_relevant, reason


def _fallback_extract(sender: str, body: str, employer_domains: set[str]) -> list[ExtractedPhoneLead]:
    leads: list[ExtractedPhoneLead] = []
    for match in PHONE_RE.finditer(body or ""):
        raw_phone = match.group(0)
        normalized, display_phone, _ext = format_phone(raw_phone)
        if not normalized:
            continue
        start = max(0, match.start() - 160)
        end = min(len(body), match.end() + 200)
        raw_fragment = body[start:end]
        fragment = raw_fragment.replace("\n", " ").strip()
        fragment_l = fragment.lower()

        designation_match = DESIGNATION_RE.search(fragment)
        designation = designation_match.group(0).title() if designation_match else "Unknown"
        owner = _extract_owner_name(raw_fragment, sender)
        contact_email = _extract_contact_email(raw_fragment, sender)
        company = _sender_company(sender)
        if "interview" in fragment_l:
            purpose = "Interview coordination"
        elif "vendor" in fragment_l:
            purpose = "Vendor contact"
        elif "call me" in fragment_l or "reach me" in fragment_l:
            purpose = "Recruiter direct number"
        else:
            purpose = "Signature block phone number"
        confidence = "high" if ("regards" in fragment_l or "recruiter" in fragment_l) else "medium"
        contact_type, relevance_score, is_relevant, relevance_reason = _classify_recruiter_relevance(
            sender=sender,
            purpose=purpose,
            designation=designation,
            source_fragment=fragment,
            employer_domains=employer_domains,
        )
        leads.append(
            ExtractedPhoneLead(
                phone_number_display=display_phone,
                phone_number_normalized=normalized,
                owner_name=owner or "Unknown",
                contact_email=contact_email,
                company=company or "Unknown",
                designation=designation,
                purpose=purpose,
                confidence=confidence,
                contact_type=contact_type,
                recruiter_relevance_score=relevance_score,
                is_recruiter_relevant=is_relevant,
                relevance_reason=relevance_reason,
                source_fragment=fragment[:240],
            )
        )
    return leads


def dedupe_phone_leads(leads: list[ExtractedPhoneLead]) -> list[ExtractedPhoneLead]:
    deduped: dict[str, ExtractedPhoneLead] = {}
    rank = {"high": 3, "medium": 2, "low": 1}
    for lead in leads:
        existing = deduped.get(lead.phone_number_normalized)
        if not existing:
            deduped[lead.phone_number_normalized] = lead
            continue
        lead_rank = rank.get(lead.confidence, 1)
        existing_rank = rank.get(existing.confidence, 1)
        if lead_rank > existing_rank:
            deduped[lead.phone_number_normalized] = lead
            continue
        if lead_rank == existing_rank and _lead_quality_score(lead) > _lead_quality_score(existing):
            deduped[lead.phone_number_normalized] = lead
    return list(deduped.values())


def extract_phone_leads(
    sender: str,
    subject: str,
    body: str,
    employer_domains: set[str] | None = None,
) -> list[ExtractedPhoneLead]:
    email_content = f"Sender: {sender}\nSubject: {subject}\n\n{body}"
    normalized_domains = {domain.strip().lower() for domain in (employer_domains or set()) if domain.strip()}
    ai_leads: list[ExtractedPhoneLead] = []
    try:
        ai_leads = _llm_extract(email_content)
    except Exception:
        ai_leads = []
    enriched_ai_leads: list[ExtractedPhoneLead] = []
    for lead in ai_leads:
        contact_type, relevance_score, is_relevant, relevance_reason = _classify_recruiter_relevance(
            sender=sender,
            purpose=lead.purpose,
            designation=lead.designation,
            source_fragment=lead.source_fragment,
            employer_domains=normalized_domains,
        )
        enriched_ai_leads.append(
            ExtractedPhoneLead(
                phone_number_display=lead.phone_number_display,
                phone_number_normalized=lead.phone_number_normalized,
                owner_name=lead.owner_name,
                contact_email=lead.contact_email,
                company=lead.company,
                designation=lead.designation,
                purpose=lead.purpose,
                confidence=lead.confidence,
                contact_type=contact_type,
                recruiter_relevance_score=relevance_score,
                is_recruiter_relevant=is_relevant,
                relevance_reason=relevance_reason,
                source_fragment=lead.source_fragment,
            )
        )
    fallback_leads = _fallback_extract(sender, body, normalized_domains)
    combined = enriched_ai_leads + fallback_leads
    return dedupe_phone_leads(combined)
