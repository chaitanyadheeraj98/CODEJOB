from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings
from app.premium_numbers.phone_normalization import canonicalize_phone
from app.premium_numbers.prompting import build_premium_numbers_prompts

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s().]{7,}\d)")
JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
DESIGNATION_RE = re.compile(
    r"\b(recruiter|bench sales recruiter|talent acquisition|hiring manager|account manager|vendor)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedPhoneLead:
    phone_number_display: str
    phone_number_normalized: str
    owner_name: str
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
    return canonicalize_phone(raw)


def _display_phone(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.strip())


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


def _domain_from_email(value: str) -> str:
    text = (value or "").strip().lower()
    if "<" in text and ">" in text:
        text = text.split("<", 1)[1].split(">", 1)[0].strip()
    if "@" not in text:
        return ""
    return text.split("@", 1)[1].strip()


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
        normalized = _normalize_phone(raw_phone)
        if not normalized:
            continue
        start = max(0, match.start() - 80)
        end = min(len(body), match.end() + 80)
        fragment = body[start:end].replace("\n", " ").strip()
        fragment_l = fragment.lower()

        designation_match = DESIGNATION_RE.search(fragment)
        designation = designation_match.group(0).title() if designation_match else "Unknown"
        owner = _sender_name(sender)
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
                phone_number_display=_display_phone(raw_phone),
                phone_number_normalized=normalized,
                owner_name=owner or "Unknown",
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
        if rank.get(lead.confidence, 1) > rank.get(existing.confidence, 1):
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
