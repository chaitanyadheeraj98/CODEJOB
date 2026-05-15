from __future__ import annotations

import json
import re
from dataclasses import dataclass

from openai import OpenAI

from app.config import settings
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
    source_fragment: str


def _normalize_phone(raw: str) -> str:
    cleaned = raw.strip()
    has_plus = cleaned.startswith("+")
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) < 10:
        return ""
    return f"+{digits}" if has_plus else digits


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


def _fallback_extract(sender: str, body: str) -> list[ExtractedPhoneLead]:
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
        leads.append(
            ExtractedPhoneLead(
                phone_number_display=_display_phone(raw_phone),
                phone_number_normalized=normalized,
                owner_name=owner or "Unknown",
                company=company or "Unknown",
                designation=designation,
                purpose=purpose,
                confidence=confidence,
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


def extract_phone_leads(sender: str, subject: str, body: str) -> list[ExtractedPhoneLead]:
    email_content = f"Sender: {sender}\nSubject: {subject}\n\n{body}"
    ai_leads: list[ExtractedPhoneLead] = []
    try:
        ai_leads = _llm_extract(email_content)
    except Exception:
        ai_leads = []
    fallback_leads = _fallback_extract(sender, body)
    combined = ai_leads + fallback_leads
    return dedupe_phone_leads(combined)
