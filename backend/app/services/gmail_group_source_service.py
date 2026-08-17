from __future__ import annotations

import re
from dataclasses import dataclass
from email.utils import getaddresses
from typing import Iterable, Sequence


GOOGLE_GROUPS_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)@googlegroups\.com\b",
    re.IGNORECASE,
)
GOOGLE_GROUPS_UNSUB_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)\+unsubscribe@googlegroups\.com\b",
    re.IGNORECASE,
)
GOOGLE_GROUPS_URL_RE = re.compile(
    r"groups\.google\.com\/g\/([A-Za-z0-9._%+-]+)",
    re.IGNORECASE,
)
LIST_ID_RE = re.compile(r"<([^>]+)>")
SUBJECT_PREFIX_RE = re.compile(r"^\[([^\]]+)\]")


@dataclass(frozen=True)
class ConfiguredRequirementGroup:
    id: int
    display_name: str
    group_email: str
    normalized_group_email: str
    group_slug: str | None
    enabled: bool = True


@dataclass(frozen=True)
class TrustedGroupContext:
    matched: bool
    group_id: int | None = None
    group_name: str | None = None
    group_email: str | None = None
    normalized_group_email: str | None = None
    group_slug: str | None = None
    match_method: str | None = None
    confidence: float = 0.0
    trusted: bool = False


def normalize_google_group_slug(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    url_match = GOOGLE_GROUPS_URL_RE.search(value)
    if url_match:
        value = url_match.group(1)
    if "@" in value:
        direct = GOOGLE_GROUPS_EMAIL_RE.search(value) or GOOGLE_GROUPS_UNSUB_RE.search(value)
        if not direct:
            return None
        local = normalize_google_group_email(value)
        return local.split("@", 1)[0] if local else None
    value = value.strip("[]<> ").strip().strip("/")
    return value or None


def normalize_google_group_email(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    unsub_match = GOOGLE_GROUPS_UNSUB_RE.search(value)
    if unsub_match:
        return f"{unsub_match.group(1).lower()}@googlegroups.com"
    email_match = GOOGLE_GROUPS_EMAIL_RE.search(value)
    if email_match:
        return f"{email_match.group(1).lower()}@googlegroups.com"
    slug = normalize_google_group_slug(value)
    if slug:
        return f"{slug.lower()}@googlegroups.com"
    return None


def canonical_group_display_name(group_email: str, display_name: str | None = None) -> str:
    preferred = (display_name or "").strip()
    if preferred:
        return preferred
    local = (group_email or "").split("@", 1)[0].strip()
    return local or group_email


def parse_group_input_line(raw: str) -> tuple[str, str | None] | None:
    line = raw.strip()
    if not line:
        return None
    parts = [part.strip() for part in line.split("|", 1)]
    if len(parts) == 2:
        display_name, source = parts[0], parts[1]
    else:
        display_name, source = "", parts[0]
    normalized_email = normalize_google_group_email(source)
    if not normalized_email:
        return None
    return normalized_email, display_name or None


def parse_group_inputs(raw_text: str) -> list[tuple[str, str | None]]:
    parsed: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for raw_line in (raw_text or "").splitlines():
        item = parse_group_input_line(raw_line)
        if not item:
            continue
        normalized_email, display_name = item
        if normalized_email in seen:
            continue
        seen.add(normalized_email)
        parsed.append((normalized_email, display_name))
    return parsed


def _extract_candidate_emails(value: str | None) -> list[str]:
    text = value or ""
    results: list[str] = []
    for _name, email in getaddresses([text]):
        normalized = normalize_google_group_email(email)
        if normalized and normalized not in results:
            results.append(normalized)
    for pattern in (GOOGLE_GROUPS_UNSUB_RE, GOOGLE_GROUPS_EMAIL_RE):
        for match in pattern.finditer(text):
            normalized = normalize_google_group_email(match.group(0))
            if normalized and normalized not in results:
                results.append(normalized)
    url_match = GOOGLE_GROUPS_URL_RE.search(text)
    if url_match:
        normalized = normalize_google_group_email(url_match.group(0))
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def _extract_list_id_candidates(value: str | None) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    results: list[str] = []
    direct = normalize_google_group_email(text)
    if direct:
        results.append(direct)
    for match in LIST_ID_RE.finditer(text):
        inner = match.group(1).strip()
        local = inner.split(".", 1)[0]
        normalized = normalize_google_group_email(local)
        if normalized and normalized not in results:
            results.append(normalized)
    return results


def _match_by_candidates(
    groups_by_email: dict[str, ConfiguredRequirementGroup],
    candidates: Iterable[str],
    *,
    match_method: str,
    confidence: float,
) -> TrustedGroupContext | None:
    for candidate in candidates:
        group = groups_by_email.get(candidate)
        if not group or not group.enabled:
            continue
        return TrustedGroupContext(
            matched=True,
            group_id=group.id,
            group_name=canonical_group_display_name(group.group_email, group.display_name),
            group_email=group.group_email,
            normalized_group_email=group.normalized_group_email,
            group_slug=group.group_slug,
            match_method=match_method,
            confidence=confidence,
            trusted=True,
        )
    return None


def resolve_trusted_group_context(
    *,
    groups: Sequence[ConfiguredRequirementGroup],
    subject: str,
    body: str,
    to_header: str | None = None,
    cc_header: str | None = None,
    list_id: str | None = None,
    list_post: str | None = None,
    list_unsubscribe: str | None = None,
    delivered_to: str | None = None,
    mailing_list: str | None = None,
) -> TrustedGroupContext:
    if not groups:
        return TrustedGroupContext(matched=False)

    groups_by_email = {group.normalized_group_email: group for group in groups if group.enabled}
    ordered_checks = [
        ("list_post", 1.0, _extract_candidate_emails(list_post)),
        ("list_unsubscribe", 0.98, _extract_candidate_emails(list_unsubscribe)),
        ("list_id", 0.95, _extract_list_id_candidates(list_id)),
        ("to_header", 0.92, _extract_candidate_emails(to_header)),
        ("cc_header", 0.9, _extract_candidate_emails(cc_header)),
        ("delivered_to", 0.88, _extract_candidate_emails(delivered_to)),
        ("mailing_list", 0.86, _extract_candidate_emails(mailing_list)),
    ]
    for match_method, confidence, candidates in ordered_checks:
        match = _match_by_candidates(groups_by_email, candidates, match_method=match_method, confidence=confidence)
        if match:
            return match

    subject_match = SUBJECT_PREFIX_RE.match((subject or "").strip())
    if subject_match:
        subject_email = normalize_google_group_email(subject_match.group(1))
        match = _match_by_candidates(groups_by_email, [subject_email] if subject_email else [], match_method="subject_prefix", confidence=0.8)
        if match:
            return match

    footer_candidates = _extract_candidate_emails(body)
    match = _match_by_candidates(groups_by_email, footer_candidates, match_method="footer_fallback", confidence=0.65)
    if match:
        return match

    return TrustedGroupContext(matched=False)
