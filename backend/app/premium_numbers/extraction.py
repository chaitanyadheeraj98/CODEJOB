from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from functools import lru_cache

from sqlalchemy.orm import Session

from app.ai.deepseek_client import deepseek_json_completion
from app.config import settings
from app.models import PremiumNumberExtractionAudit
from app.premium_numbers.phone_normalization import format_phone
from app.premium_numbers.prompting import build_premium_numbers_prompts
from app.semantic.embeddings_service import _sbert_embedding

PHONE_RE = re.compile(r"(?:\+?\d[\d\-\s().]{7,}\d)")
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
URL_NOISE_TOKEN_RE = re.compile(r"%(?:[0-9A-Fa-f]{2})")
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
NOISE_CONTEXT_TERMS = (
    "unsubscribe",
    "groups.google.com",
    "view this discussion",
    "utm_",
    "msgid",
    "http://",
    "https://",
    "www.",
)
NUMERIC_LABEL_NOISE_TERMS = (
    "duration:",
    "experience:",
    "rate:",
    "salary:",
    "ctc:",
    "/hr",
    "per hour",
)
CONTACT_INTENT_TERMS = (
    "call",
    "reach",
    "phone",
    "text",
    "ext",
    "contact",
    "regards",
    "thanks",
    "recruiter",
)
SBERT_POSITIVE_PROTOTYPES = (
    "Call me at this number for recruiter follow up.",
    "Reach me on phone for job discussion.",
    "Contact recruiter directly on this number.",
    "Thanks and regards recruiter signature phone.",
)
SBERT_NEGATIVE_PROTOTYPES = (
    "Unsubscribe from this group and stop receiving emails.",
    "View this discussion on groups dot google dot com.",
    "Tracking link with utm parameters and message id token.",
    "System footer link metadata and list management notice.",
)
SBERT_MARGIN_THRESHOLD = 0.12
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedContactGroup:
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
    role: str = "unknown"
    extraction_source: str = "ai"
    linkedin_url: str = ""
    source_section: str = "unknown"
    block_id: str = ""
    evidence_text: str = ""
    evidence_offset_start: int | None = None
    evidence_offset_end: int | None = None
    colocation_verified: bool = False


# Backward-compatible import name for existing callers outside the shared workflow.
ExtractedPhoneLead = ExtractedContactGroup


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


def record_extraction_audit(
    db: Session | None,
    *,
    owner_id: str,
    source_email_id: int | None,
    source_external_opportunity_id: int | None,
    raw_value: str,
    normalized_value: str | None,
    status: str,
    stage: str,
    reason: str,
) -> None:
    if db is None:
        return
    db.add(
        PremiumNumberExtractionAudit(
            owner_id=owner_id,
            source_email_id=source_email_id,
            source_external_opportunity_id=source_external_opportunity_id,
            raw_value=(raw_value or "")[:120],
            normalized_value=normalized_value or None,
            status=status,
            stage=stage,
            reason=(reason or "")[:160],
        )
    )


def _llm_extract(
    email_content: str,
    employer_domains: set[str],
    *,
    db: Session | None = None,
    owner_id: str = "",
    source_email_id: int | None = None,
    source_external_opportunity_id: int | None = None,
) -> list[ExtractedContactGroup]:
    system_prompt, user_prompt = build_premium_numbers_prompts(email_content, employer_domains)
    # thinking="disabled": deepseek-v4-flash is a reasoning model that otherwise spends the
    # max_tokens budget on invisible chain-of-thought and returns empty content (finish_reason
    # "length") before writing the actual JSON answer - see docs/temp files/temp127.md.
    payload = deepseek_json_completion(
        system_prompt,
        user_prompt,
        max_tokens=900,
        thinking="disabled",
    )
    items = payload.get("contacts")
    if not isinstance(items, list):
        return []

    leads: list[ExtractedContactGroup] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_phone = str(item.get("phone_number", ""))
        display = _display_phone(raw_phone)
        normalized = _normalize_phone(display)
        contact_email = str(item.get("email", "")).strip().lower()
        if not normalized:
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=raw_phone,
                normalized_value=None,
                status="rejected",
                stage="noise_prefilter",
                reason="invalid_phone_shape" if raw_phone.strip() else "phone_absent",
            )
            display = ""
            if not contact_email:
                # No phone and no email: nothing to key this contact on later, so it
                # can't be reliably re-found on the next rescore. Drop it, same as before.
                continue
        source_section = str(item.get("source_section", "unknown")).strip().lower()
        if source_section not in {"body", "signature", "unknown"}:
            source_section = "unknown"
        evidence_text = str(item.get("evidence_text", "")).strip()
        leads.append(
            ExtractedContactGroup(
                role=str(item.get("role", "unknown")).strip().lower()
                if str(item.get("role", "unknown")).strip().lower() in {"recruiter", "employer", "unknown"}
                else "unknown",
                phone_number_display=display,
                phone_number_normalized=normalized,
                owner_name=str(item.get("name", item.get("owner_name", "Unknown"))).strip() or "Unknown",
                contact_email=contact_email,
                company=str(item.get("company", "Unknown")).strip() or "Unknown",
                designation=str(item.get("designation", "Unknown")).strip() or "Unknown",
                purpose=str(item.get("purpose", "Recruiter contact")).strip() or "Recruiter contact",
                confidence=_normalize_confidence(str(item.get("confidence", "low"))),
                contact_type="unknown",
                recruiter_relevance_score=0,
                is_recruiter_relevant=False,
                relevance_reason="llm_only_unclassified",
                source_fragment=evidence_text or "Extracted by AI from email context",
                extraction_source="ai",
                linkedin_url=str(item.get("linkedin_url", "")).strip(),
                source_section=source_section,
                block_id=str(item.get("block_id", "")).strip()[:64],
                evidence_text=evidence_text,
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


def _is_employer_domain(email: str, employer_domains: set[str]) -> bool:
    domain = _domain_from_email(email)
    return bool(domain) and domain in employer_domains


def _extract_contact_email(fragment: str, sender: str, employer_domains: set[str]) -> str:
    matches = [m for m in EMAIL_RE.findall(fragment or "") if not _is_employer_domain(m, employer_domains)]
    if matches:
        return matches[-1].lower()
    return _sender_email(sender)


def _extract_owner_name(fragment: str, sender: str, employer_domains: set[str]) -> str:
    # Prefer explicit target-contact contexts (e.g. "share resume to rabbanis@...").
    contact_match = TARGET_CONTACT_EMAIL_RE.search(fragment or "")
    if contact_match:
        target_email = contact_match.group(1).strip()
        if not _is_employer_domain(target_email, employer_domains):
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
        if _is_employer_domain(match.group(0), employer_domains):
            continue
        local = re.sub(r"[^A-Za-z]", "", match.group(1) or "").strip()
        if len(local) < 2:
            continue
        if local.lower() in GENERIC_LOCAL_NAME_TOKENS:
            continue
        return local.title()
    return _sender_name(sender)


def _verify_colocation(
    evidence_text: str,
    phone_raw: str,
    email_content: str,
    *,
    email_raw: str = "",
    max_distance: int = 500,
) -> tuple[bool, int | None]:
    # No phone: anchor the evidence to the recruiter's email address instead, same
    # proximity check, same anti-hallucination guarantee.
    evidence_parts = (evidence_text or "").split()
    phone_normalized = _normalize_phone(phone_raw)
    anchor_email = (email_raw or "").strip().lower()
    if not evidence_parts or not (phone_normalized or anchor_email):
        return False, None

    collapsed: list[str] = []
    offsets: list[int] = []
    for index, char in enumerate(email_content or ""):
        if char.isspace():
            if collapsed and collapsed[-1] != " ":
                collapsed.append(" ")
                offsets.append(index)
        else:
            collapsed.append(char.casefold())
            offsets.append(index)
    normalized_evidence = " ".join(evidence_parts).casefold()
    evidence_at = "".join(collapsed).find(normalized_evidence)
    if evidence_at < 0:
        return False, None
    evidence_start = offsets[evidence_at]
    evidence_end = offsets[evidence_at + len(normalized_evidence) - 1] + 1
    if phone_normalized:
        anchor_spans = [
            match.span()
            for match in PHONE_RE.finditer(email_content or "")
            if _normalize_phone(match.group(0)) == phone_normalized
        ]
    else:
        anchor_spans = [
            match.span()
            for match in EMAIL_RE.finditer(email_content or "")
            if match.group(0).lower() == anchor_email
        ]
    if not anchor_spans:
        return False, evidence_start
    distance = min(
        max(evidence_start - anchor_end, anchor_start - evidence_end, 0)
        for anchor_start, anchor_end in anchor_spans
    )
    return distance <= max_distance, evidence_start


def _group_candidates_by_block(leads: list[ExtractedContactGroup]) -> list[ExtractedContactGroup]:
    grouped: dict[str, list[ExtractedContactGroup]] = {}
    for lead in leads:
        if lead.block_id:
            grouped.setdefault(lead.block_id, []).append(lead)

    identities = {
        block_id: max(items, key=_lead_quality_score)
        for block_id, items in grouped.items()
    }
    return [
        replace(
            lead,
            owner_name=identities[lead.block_id].owner_name,
            contact_email=identities[lead.block_id].contact_email,
            company=identities[lead.block_id].company,
            designation=identities[lead.block_id].designation,
        )
        if lead.block_id in identities
        else lead
        for lead in leads
    ]


def _lead_quality_score(lead: ExtractedContactGroup) -> int:
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
    candidate_email: str = "",
    sender: str,
    purpose: str,
    designation: str,
    source_fragment: str,
    employer_domains: set[str],
) -> tuple[str, int, bool, str]:
    score = 50
    reasons: list[str] = []
    domain = _domain_from_email(candidate_email) or _domain_from_email(sender)
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


def _looks_like_weak_numeric_candidate(raw_phone: str) -> bool:
    cleaned = (raw_phone or "").strip()
    if not cleaned:
        return True
    has_plus = "+" in cleaned
    has_formatting = any(ch in cleaned for ch in ("(", ")", "-", ".", " "))
    digits = re.sub(r"\D", "", cleaned)
    return len(digits) == 10 and not has_plus and not has_formatting


def _has_contact_intent(fragment: str) -> bool:
    text = (fragment or "").lower()
    return any(term in text for term in CONTACT_INTENT_TERMS)


def _is_noise_context(raw_fragment: str, normalized_fragment: str, raw_phone: str) -> bool:
    raw_text = (raw_fragment or "").lower()
    norm_text = (normalized_fragment or "").lower()
    if any(term in raw_text for term in NOISE_CONTEXT_TERMS):
        return True
    if any(term in norm_text for term in NOISE_CONTEXT_TERMS):
        return True
    phone_at = raw_text.find((raw_phone or "").lower())
    proximity = (
        raw_text[max(0, phone_at - 48):phone_at + len(raw_phone) + 24]
        if phone_at >= 0
        else raw_text
    )
    if any(term in proximity for term in NUMERIC_LABEL_NOISE_TERMS):
        return True
    if "mailto:" in raw_text and ("unsubscribe" in raw_text or "googlegroups" in raw_text):
        return True
    if "mailto:" in norm_text and ("unsubscribe" in norm_text or "googlegroups" in norm_text):
        return True
    if URL_NOISE_TOKEN_RE.search(raw_phone or ""):
        return True
    return False


def _dot(lhs: list[float], rhs: list[float]) -> float:
    if not lhs or not rhs:
        return 0.0
    size = min(len(lhs), len(rhs))
    return sum(lhs[i] * rhs[i] for i in range(size))


@lru_cache(maxsize=1)
def _sbert_prototype_centroids() -> tuple[list[float], list[float]]:
    model_name = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
    positive_vectors = [_sbert_embedding(text, model_name)[0] for text in SBERT_POSITIVE_PROTOTYPES]
    negative_vectors = [_sbert_embedding(text, model_name)[0] for text in SBERT_NEGATIVE_PROTOTYPES]

    def _avg(vectors: list[list[float]]) -> list[float]:
        if not vectors:
            return []
        dims = min(len(v) for v in vectors if v)
        if dims <= 0:
            return []
        sums = [0.0] * dims
        for vector in vectors:
            for idx in range(dims):
                sums[idx] += vector[idx]
        return [item / len(vectors) for item in sums]

    return _avg(positive_vectors), _avg(negative_vectors)


def _sbert_keep_candidate(fragment: str) -> tuple[bool, str]:
    model_name = settings.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2"
    if not fragment.strip():
        return True, "sbert_skipped"
    try:
        candidate_vec, _provider = _sbert_embedding(fragment, model_name)
        positive_centroid, negative_centroid = _sbert_prototype_centroids()
        positive_sim = _dot(candidate_vec, positive_centroid)
        negative_sim = _dot(candidate_vec, negative_centroid)
        margin = positive_sim - negative_sim
        keep = margin >= SBERT_MARGIN_THRESHOLD
        return keep, f"sbert_margin={margin:.3f}"
    except Exception:
        return True, "sbert_error"


def _fallback_extract(
    sender: str,
    body: str,
    employer_domains: set[str],
    *,
    extraction_source: str = "regex_fallback",
    db: Session | None = None,
    owner_id: str = "",
    source_email_id: int | None = None,
    source_external_opportunity_id: int | None = None,
) -> list[ExtractedContactGroup]:
    leads: list[ExtractedContactGroup] = []
    for match in PHONE_RE.finditer(body or ""):
        raw_phone = match.group(0)
        normalized, display_phone, _ext = format_phone(raw_phone)
        if not normalized:
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=raw_phone,
                normalized_value=None,
                status="rejected",
                stage="international_unsupported" if raw_phone.strip().startswith("+") else "noise_prefilter",
                reason="unsupported_phone_shape",
            )
            continue
        start = max(0, match.start() - 160)
        end = min(len(body), match.end() + 200)
        raw_fragment = body[start:end]
        fragment = raw_fragment.replace("\n", " ").strip()
        if _is_noise_context(raw_fragment, fragment, raw_phone):
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=raw_phone,
                normalized_value=normalized,
                status="rejected",
                stage="noise_prefilter",
                reason="numeric_or_footer_noise_context",
            )
            continue
        if _looks_like_weak_numeric_candidate(raw_phone) and not _has_contact_intent(fragment):
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=raw_phone,
                normalized_value=normalized,
                status="rejected",
                stage="noise_prefilter",
                reason="weak_numeric_candidate_without_contact_intent",
            )
            continue
        sbert_keep, sbert_reason = _sbert_keep_candidate(fragment)
        if not sbert_keep:
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=raw_phone,
                normalized_value=normalized,
                status="rejected",
                stage="sbert",
                reason=sbert_reason,
            )
            continue
        fragment_l = fragment.lower()

        designation_match = DESIGNATION_RE.search(fragment)
        designation = designation_match.group(0).title() if designation_match else "Unknown"
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
            candidate_email="",
            sender=sender,
            purpose=purpose,
            designation=designation,
            source_fragment=fragment,
            employer_domains=employer_domains,
        )
        if sbert_reason:
            relevance_reason = f"{relevance_reason},{sbert_reason}" if relevance_reason else sbert_reason
        leads.append(
            ExtractedContactGroup(
                role="unknown",
                phone_number_display=display_phone,
                phone_number_normalized=normalized,
                owner_name="Unknown",
                contact_email="",
                company="Unknown",
                designation=designation,
                purpose=purpose,
                confidence=confidence,
                contact_type=contact_type,
                recruiter_relevance_score=relevance_score,
                is_recruiter_relevant=is_relevant,
                relevance_reason=relevance_reason,
                source_fragment=fragment[:240],
                extraction_source=extraction_source,
                source_section="body",
                evidence_text=fragment[:240],
                evidence_offset_start=match.start(),
                evidence_offset_end=match.end(),
                colocation_verified=True,
            )
        )
    return leads


def dedupe_phone_leads(leads: list[ExtractedContactGroup]) -> list[ExtractedContactGroup]:
    deduped: dict[tuple[str, str], ExtractedContactGroup] = {}
    rank = {"high": 3, "medium": 2, "low": 1}
    for lead in leads:
        key = (lead.phone_number_normalized, lead.role)
        existing = deduped.get(key)
        if not existing:
            deduped[key] = lead
            continue
        lead_rank = rank.get(lead.confidence, 1)
        existing_rank = rank.get(existing.confidence, 1)
        if lead_rank > existing_rank:
            deduped[key] = lead
            continue
        if lead_rank == existing_rank and _lead_quality_score(lead) > _lead_quality_score(existing):
            deduped[key] = lead
    return list(deduped.values())


def _finalize_extraction(
    leads: list[ExtractedContactGroup],
    *,
    db: Session | None,
    owner_id: str,
    source_email_id: int | None,
    source_external_opportunity_id: int | None,
) -> list[ExtractedContactGroup]:
    deduped = dedupe_phone_leads(leads)
    kept_ids = {id(lead) for lead in deduped}
    for lead in leads:
        if id(lead) not in kept_ids:
            record_extraction_audit(
                db,
                owner_id=owner_id,
                source_email_id=source_email_id,
                source_external_opportunity_id=source_external_opportunity_id,
                raw_value=lead.phone_number_display,
                normalized_value=lead.phone_number_normalized,
                status="rejected",
                stage="dedupe",
                reason="duplicate_phone_and_role_in_source",
            )
    for lead in deduped:
        if not lead.colocation_verified:
            status, stage, reason = "rejected", "colocation_check", "source_attribution_failure"
        elif lead.phone_number_normalized.startswith("+"):
            status, stage, reason = "rejected", "international_unsupported", "international_number_needs_verification"
        else:
            status, stage, reason = "accepted", "accepted", "candidate_accepted"
        record_extraction_audit(
            db,
            owner_id=owner_id,
            source_email_id=source_email_id,
            source_external_opportunity_id=source_external_opportunity_id,
            raw_value=lead.phone_number_display,
            normalized_value=lead.phone_number_normalized,
            status=status,
            stage=stage,
            reason=reason,
        )
    return deduped


def extract_phone_leads(
    sender: str,
    subject: str,
    body: str,
    employer_domains: set[str] | None = None,
    *,
    db: Session | None = None,
    owner_id: str = "",
    source_email_id: int | None = None,
    source_external_opportunity_id: int | None = None,
) -> list[ExtractedContactGroup]:
    email_content = f"Sender: {sender}\nSubject: {subject}\n\n{body}"
    normalized_domains = {domain.strip().lower() for domain in (employer_domains or set()) if domain.strip()}
    ai_leads: list[ExtractedContactGroup] = []
    ai_unavailable = not bool(settings.deepseek_api_key)
    if not ai_unavailable:
        try:
            ai_leads = (
                _llm_extract(email_content, normalized_domains)
                if db is None
                else _llm_extract(
                    email_content,
                    normalized_domains,
                    db=db,
                    owner_id=owner_id,
                    source_email_id=source_email_id,
                    source_external_opportunity_id=source_external_opportunity_id,
                )
            )
        except Exception:
            ai_unavailable = True
            logger.exception("Premium-number AI extraction failed; using regex fallback")
            ai_leads = []

    if ai_leads:
        # AI ran and found at least one contact - trust it exclusively. Regex is a fallback
        # for when AI is disabled/unavailable/fails/finds nothing, not a second opinion to
        # merge in alongside a successful AI result.
        enriched_ai_leads: list[ExtractedContactGroup] = []
        for lead in _group_candidates_by_block(ai_leads):
            evidence_text = lead.evidence_text or lead.source_fragment
            colocation_verified, evidence_start = _verify_colocation(
                evidence_text,
                lead.phone_number_display,
                email_content,
                email_raw=lead.contact_email,
            )
            contact_type, relevance_score, is_relevant, relevance_reason = _classify_recruiter_relevance(
                candidate_email=lead.contact_email,
                sender=sender,
                purpose=lead.purpose,
                designation=lead.designation,
                source_fragment=lead.source_fragment,
                employer_domains=normalized_domains,
            )
            role = lead.role
            if _is_employer_domain(lead.contact_email, normalized_domains):
                role = "employer"
            elif lead.role != "employer" and is_relevant:
                role = "recruiter"
            if not colocation_verified:
                relevance_reason = f"{relevance_reason},source_attribution_failure"
            enriched_ai_leads.append(
                replace(
                    lead,
                    role=role,
                    confidence=lead.confidence if colocation_verified else "low",
                    contact_type=contact_type,
                    recruiter_relevance_score=relevance_score,
                    is_recruiter_relevant=is_relevant,
                    relevance_reason=relevance_reason,
                    extraction_source="ai",
                    evidence_text=evidence_text,
                    evidence_offset_start=evidence_start,
                    evidence_offset_end=(evidence_start + len(evidence_text)) if evidence_start is not None else None,
                    colocation_verified=colocation_verified,
                )
            )
        return _finalize_extraction(
            enriched_ai_leads,
            db=db,
            owner_id=owner_id,
            source_email_id=source_email_id,
            source_external_opportunity_id=source_external_opportunity_id,
        )

    # AI disabled, unavailable, failed, or found nothing - regex is the sole source.
    fallback_leads = _fallback_extract(
        sender,
        body,
        normalized_domains,
        extraction_source="regex_fallback_ai_unavailable" if ai_unavailable else "regex_fallback",
        db=db,
        owner_id=owner_id,
        source_email_id=source_email_id,
        source_external_opportunity_id=source_external_opportunity_id,
    )
    return _finalize_extraction(
        fallback_leads,
        db=db,
        owner_id=owner_id,
        source_email_id=source_email_id,
        source_external_opportunity_id=source_external_opportunity_id,
    )
