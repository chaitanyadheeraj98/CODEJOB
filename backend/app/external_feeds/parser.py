from __future__ import annotations

import html
import logging
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse

from app.parsing.document_extraction import clean_html_text
from app.skill_taxonomy import extract_skills_text, normalize_skills_text

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - fallback path for minimal envs
    BeautifulSoup = None

from .types import ParsedExternalPost, ParsedListingRow, ParsedNvoidsDetail


logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})")
_PHONE_LABEL_RE = re.compile(
    r"(?:phone|mobile|contact|call|reach(?:\s+me)?(?:\s+at)?)\s*[:\-]?\s*"
    r"((?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}))",
    re.IGNORECASE,
)
_ROW3_PHONE_LABEL_RE = re.compile(
    r"(?:phone(?:\s*no\.?)?|ph(?:\s*no\.?)?|mobile|contact|call|reach(?:\s+me)?(?:\s+at)?|cell(?:\s*no\.?)?)\s*[:\-]?\s*"
    r"((?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}))",
    re.IGNORECASE,
)
_FROM_LINE_RE = re.compile(r"^\s*from\s*:\s*(.+)$", re.IGNORECASE)
_NOISE_LINE_RE = re.compile(
    r"(?:job_kill|time taken|cloudflare|googletagmanager|server timeout|page[s]? not loading|cf-beacon|data-cfemail)",
    re.IGNORECASE,
)
_HTML_ROLE_NOISE_RE = re.compile(
    r"(?:<br|</|<td|<tr|href=|data-cfemail|job description|thanks and regards)",
    re.IGNORECASE,
)
_SKIP_DETAIL_TITLE_RE = re.compile(
    r"^(?:home|email\s*:|from\s*:|http://|https://|www\.|hi\b|hope\b|job description\b|location\s*:|long term contract\b|thanks\b|thanks and regards\b|regards\b)",
    re.IGNORECASE,
)
_POSTED_TEXT_RE = re.compile(r"\b(?:0?[1-9]|1[0-2]):[0-5]\d\s*(?:AM|PM)\s+\d{1,2}-[A-Za-z]{3}-\d{2}\b", re.IGNORECASE)
_ROLE_LABEL_RE = re.compile(r"^\s*(?:job\s*title|job\s*role|role)\s*[:\-]?\s*(.*)$", re.IGNORECASE)
_LOCATION_LABEL_RE = re.compile(r"^\s*location\s*[:\-]?\s*(.*)$", re.IGNORECASE)
_GENERIC_ROW_NOISE_RE = re.compile(
    r"(?:^view\s+all$|posts?\s+from\s+recruiter|search\s+results|a collection of search strings|job_kill|pages?\s+not\s+loading|time\s+taken|admin|server timeout)",
    re.IGNORECASE,
)
_DETAIL_LINK_RE = re.compile(r"job_details\.jsp\?id=", re.IGNORECASE)
_ROW3_VALIDICTION_RE = re.compile(r"^(?:best regards|regards|thanks(?: and regards)?|thank you|sincerely)[,!\s]*$", re.IGNORECASE)
_ROW3_CONTEXT_RE = re.compile(
    r"(?:\bfrom\s*:|\b(?:phone(?:\s*no\.?)?|ph(?:\s*no\.?)?|mobile|contact|call|reach|cell(?:\s*no\.?)?)\b|"
    r"\bemail\s*:|@[A-Z0-9.-]+\.[A-Z]{2,}|\bbest regards\b|\bthanks(?: and regards)?\b|\bsincerely\b)",
    re.IGNORECASE,
)
_ROW3_NON_NAME_RE = re.compile(
    r"(?:\b(?:role|skills?|client|company|location|address|web|website|email|phone|mobile|contact|call|reach|ext|extension|keywords|responsibilities|job description)\b|"
    r"https?://|www\.|\d)",
    re.IGNORECASE,
)


def _normalize_line(value: str) -> str:
    line = re.sub(r"\s+", " ", str(value or "")).strip()
    return line.strip(" |:-")


def _has_meaningful_html_text(detail_html: str) -> bool:
    if not detail_html:
        return False
    text = re.sub(r"<[^>]+>", " ", detail_html)
    text = re.sub(r"\s+", " ", text).strip()
    return bool(text)


def _looks_like_listing_title(title: str) -> bool:
    normalized = _normalize_line(title)
    if not normalized:
        return False
    if _GENERIC_ROW_NOISE_RE.search(normalized):
        return False
    return True


def _decode_cfemail(value: str) -> str:
    raw = (value or "").strip()
    if len(raw) < 4 or len(raw) % 2:
        return ""
    try:
        key = int(raw[:2], 16)
        chars = [chr(int(raw[i : i + 2], 16) ^ key) for i in range(2, len(raw), 2)]
        decoded = "".join(chars)
    except ValueError:
        return ""
    match = _EMAIL_RE.search(decoded)
    return match.group(0).lower() if match else ""


def _extract_emails_from_fragment(fragment_html: str, fragment_text: str) -> list[str]:
    emails: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        normalized = candidate.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            emails.append(normalized)

    if BeautifulSoup is not None and fragment_html:
        soup = BeautifulSoup(fragment_html, "lxml")
        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if href.lower().startswith("mailto:"):
                _add(href.split(":", 1)[1].split("?", 1)[0])
        for span in soup.find_all(attrs={"data-cfemail": True}):
            decoded = _decode_cfemail(str(span.get("data-cfemail") or ""))
            if decoded:
                _add(decoded)
        for tag in soup.find_all(string=True):
            for match in _EMAIL_RE.findall(str(tag)):
                _add(match)

    for match in _EMAIL_RE.findall(fragment_text or ""):
        _add(match)
    return emails


def _normalize_multiline_text(value: str) -> str:
    lines = [re.sub(r"\s+", " ", part).strip(" |:-") for part in str(value or "").splitlines()]
    cleaned = [line for line in lines if line]
    return "\n".join(cleaned).strip()


def _extract_row_text_with_linebreaks(row_html: str, row_text: str) -> str:
    if row_html:
        text = re.sub(r"(?i)<br\s*/?>", "\n", row_html)
        text = re.sub(r"(?i)</(td|tr|div|p|li|ul|ol)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        normalized = _normalize_multiline_text(html.unescape(text))
        if normalized:
            return normalized
    return _normalize_multiline_text(row_text)


def _row_html_to_markdown(row_html: str) -> str:
    """Render a nvoids table-cell fragment through the same HTML->Markdown
    pipeline used for gmail bodies, instead of a flat line-joined string.

    unstructured's partition_html collapses bare <br> tags to spaces (they
    aren't block boundaries), so pseudo-lines are re-wrapped as <p> blocks
    first to make each one its own markdown block.
    """
    if not row_html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\x00", row_html)
    text = re.sub(r"(?i)</(td|tr|div|p|li|ul|ol|h[1-6])>", "\x00", text)
    text = re.sub(r"<[^>]+>", "", text)
    pieces = [html.unescape(piece).strip() for piece in text.split("\x00")]
    fragment = "".join(f"<p>{html.escape(piece)}</p>" for piece in pieces if piece)
    if not fragment:
        return ""
    return clean_html_text(fragment).strip()


def _extract_nvoids_table_rows(detail_html: str) -> tuple[list[str], list[str]]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(detail_html or "", "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        table = soup.find("table")
        if table is None:
            return [], []
        row_texts: list[str] = []
        row_htmls: list[str] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            text = _normalize_line(" ".join(cell.get_text(" ", strip=True) for cell in cells) if cells else row.get_text(" ", strip=True))
            html = "".join(str(cell) for cell in cells) if cells else str(row)
            if text:
                row_texts.append(text)
                row_htmls.append(html)
                if len(row_texts) >= 5:
                    break
        return row_texts, row_htmls

    table_match = re.search(r"<table[^>]*>(.*?)</table>", detail_html or "", flags=re.IGNORECASE | re.DOTALL)
    if not table_match:
        return [], []
    segments = re.findall(r"<tr[^>]*>(.*?)</tr>", table_match.group(1), flags=re.IGNORECASE | re.DOTALL)
    row_texts: list[str] = []
    row_htmls: list[str] = []
    for segment in segments:
        text = re.sub(r"<[^>]+>", " ", segment)
        text = _normalize_line(text)
        if text:
            row_texts.append(text)
            row_htmls.append(segment)
            if len(row_texts) >= 5:
                break
    return row_texts, row_htmls


def _extract_row_email(row_html: str, row_text: str) -> str:
    emails = _extract_emails_from_fragment(row_html, row_text)
    return emails[0] if emails else ""


def _split_role_location_from_row1(row_text: str) -> tuple[str, str]:
    normalized = _normalize_line(row_text)
    if not normalized:
        return "role not fetched", ""
    parts = re.split(r"\s+at\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) != 2:
        return normalized or "role not fetched", ""
    role = _normalize_line(parts[0]) or "role not fetched"
    location = _normalize_line(parts[1])
    return role, location


def _extract_recruiter_name_from_row3(row3_text: str) -> str:
    candidate = _extract_recruiter_name(row3_text)
    if candidate:
        return candidate
    lines = [line.strip() for line in _normalize_multiline_text(row3_text).splitlines() if line.strip()]
    if lines:
        first = _normalize_line(lines[0])
        has_contact_context = any(
            _PHONE_RE.search(line) or _EMAIL_RE.search(line) or line.lower().startswith("from:")
            for line in lines[1:]
        )
        if has_contact_context and first and not _EMAIL_RE.search(first) and not _PHONE_RE.search(first):
            if re.fullmatch(r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){1,3}", first):
                return first.strip(" ,.-:")
    for index, line in enumerate(lines):
        if not _ROW3_VALIDICTION_RE.match(line):
            continue
        for follower in lines[index + 1 : index + 4]:
            candidate = _normalize_line(follower)
            if _looks_like_row3_person_name(candidate):
                return candidate.strip(" ,.-:")
    for index, line in enumerate(lines):
        lower = line.lower()
        if lower.startswith("reply to") or lower.startswith("email:"):
            if index > 0:
                prev = _normalize_line(lines[index - 1])
                if prev and not _EMAIL_RE.search(prev) and not _PHONE_RE.search(prev):
                    if _looks_like_row3_person_name(prev):
                        return prev.strip(" ,.-:")
        if _EMAIL_RE.search(line):
            for prev in reversed(lines[max(0, index - 3) : index]):
                candidate = _normalize_line(prev)
                if _looks_like_row3_person_name(candidate):
                    return candidate.strip(" ,.-:")
    return ""


def _looks_like_row3_person_name(value: str) -> bool:
    candidate = _normalize_line(value)
    if not candidate:
        return False
    if _EMAIL_RE.search(candidate) or _PHONE_RE.search(candidate):
        return False
    if _ROW3_NON_NAME_RE.search(candidate):
        return False
    return bool(re.fullmatch(r"[A-Z][A-Za-z.'-]*(?: [A-Z][A-Za-z.'-]*){1,3}", candidate))


def _extract_row3_recruiter_phone(row3_text: str) -> str:
    normalized = _normalize_multiline_text(row3_text)
    if not normalized:
        return ""

    label_match = _ROW3_PHONE_LABEL_RE.search(normalized)
    if label_match:
        return re.sub(r"\s+", " ", label_match.group(1)).strip()

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if not lines:
        return ""
    if not any(_ROW3_CONTEXT_RE.search(line) for line in lines):
        return ""

    for index, line in enumerate(lines):
        if _NOISE_LINE_RE.search(line):
            continue
        candidate_match = _PHONE_RE.search(line)
        if not candidate_match:
            continue
        window_lines = lines[max(0, index - 3) : min(len(lines), index + 2)]
        window_text = "\n".join(window_lines)
        if _NOISE_LINE_RE.search(window_text):
            continue
        if any(_ROW3_CONTEXT_RE.search(window_line) for window_line in window_lines if window_line != line):
            return re.sub(r"\s+", " ", candidate_match.group(0)).strip()
        if any(_ROW3_VALIDICTION_RE.match(window_line) for window_line in window_lines):
            return re.sub(r"\s+", " ", candidate_match.group(0)).strip()
        if any(_looks_like_row3_person_name(window_line) for window_line in window_lines if window_line != line):
            return re.sub(r"\s+", " ", candidate_match.group(0)).strip()
    return ""


def _fallback_nvoids_detail(fallback_title: str, fallback_location: str) -> ParsedNvoidsDetail:
    normalized_title = _normalize_line(fallback_title)
    _ = fallback_location
    return ParsedNvoidsDetail(
        listing_subject=normalized_title,
        recruiter_email="",
        recruiter_phone="",
        recruiter_name="",
        body="",
        jd_body="",
        jd_body_source="",
        repeated_email="",
        posted_text="",
        role="role not fetched",
        location="",
        raw_table_text="",
        parse_confidence=0.15,
    )


def parse_nvoids_detail(detail_html: str, fallback_title: str, fallback_location: str) -> ParsedNvoidsDetail:
    if not (detail_html or "").strip():
        logger.info("nvoids_parse_detail_skipped_empty_html fallback_title=%r", fallback_title)
        return _fallback_nvoids_detail(fallback_title, fallback_location)
    row_texts, row_htmls = _extract_nvoids_table_rows(detail_html)
    if len(row_texts) < 5 or len(row_htmls) < 5:
        logger.info("nvoids_parse_detail_strict_fallback_insufficient_rows rows=%s fallback_title=%r", len(row_texts), fallback_title)
        return _fallback_nvoids_detail(fallback_title, fallback_location)

    listing_subject = _normalize_line(row_texts[0]) or _normalize_line(fallback_title)
    role, location = _split_role_location_from_row1(row_texts[0])
    recruiter_email = _extract_row_email(row_htmls[1], row_texts[1])
    jd_body_plain = _extract_row_text_with_linebreaks(row_htmls[2], row_texts[2]).strip()
    recruiter_phone = _extract_row3_recruiter_phone(jd_body_plain)
    recruiter_name = _extract_recruiter_name_from_row3(jd_body_plain)
    jd_body = _row_html_to_markdown(row_htmls[2]).strip() or jd_body_plain
    jd_body_source = "nvoids_detail_table_row_3" if jd_body else ""
    repeated_email = _extract_row_email(row_htmls[3], row_texts[3])
    posted_match = _POSTED_TEXT_RE.search(row_texts[4])
    posted_text = _normalize_line(posted_match.group(0)) if posted_match else ""
    body = jd_body
    confidence = 0.35
    if listing_subject:
        confidence += 0.2
    if role and role != "role not fetched":
        confidence += 0.15
    if recruiter_email:
        confidence += 0.1
    if jd_body:
        confidence += 0.1
    if posted_text:
        confidence += 0.1
    if location:
        confidence += 0.05
    raw_table_text = "\n".join(_normalize_line(text) for text in row_texts[:5]).strip()
    return ParsedNvoidsDetail(
        listing_subject=listing_subject or _normalize_line(fallback_title),
        recruiter_email=recruiter_email,
        recruiter_phone=recruiter_phone,
        recruiter_name=recruiter_name,
        body=body.strip(),
        jd_body=jd_body.strip(),
        jd_body_source=jd_body_source,
        repeated_email=repeated_email,
        posted_text=posted_text,
        role=role,
        location=location,
        raw_table_text=raw_table_text,
        parse_confidence=min(confidence, 0.98),
    )


def extract_nvoids_detail_title(detail_html: str, fallback_title: str) -> str:
    row_texts, _row_htmls = _extract_nvoids_table_rows(detail_html)
    if len(row_texts) >= 1:
        normalized = _normalize_line(row_texts[0])
        if normalized:
            return normalized
    return _normalize_line(fallback_title) or fallback_title


def extract_nvoids_page_title(detail_html: str) -> str:
    if not detail_html:
        return ""
    if BeautifulSoup is not None:
        soup = BeautifulSoup(detail_html, "lxml")
        title = _normalize_line(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title:
            return title
    match = re.search(r"<title[^>]*>(.*?)</title>", detail_html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title_text = re.sub(r"<[^>]+>", " ", match.group(1))
    return _normalize_line(title_text)


def classify_nvoids_page_title(title: str) -> str:
    normalized = re.sub(r"\s+", " ", str(title or "")).strip().lower()
    if normalized == "job details":
        return "job_details"
    if normalized in {"hotlist details", "hotlists details"}:
        return "hotlist_details"
    return "unknown"


def extract_nvoids_clean_body(detail_html: str, fallback_title: str, location: str) -> str:
    detail = parse_nvoids_detail(detail_html, fallback_title, location)
    parts = [detail.role]
    if detail.location and detail.location.lower() not in detail.role.lower():
        parts.append(detail.location)
    if detail.jd_body:
        parts.append(detail.jd_body)
    return "\n".join(parts).strip()


def parse_listing_rows(html: str, base_url: str) -> list[ParsedListingRow]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html, "lxml")
        rows: list[ParsedListingRow] = []
        for link in soup.find_all("a"):
            title = (link.get_text(" ", strip=True) or "").strip()
            href = (link.get("href") or "").strip()
            if not title or not href:
                continue
            if href.startswith("mailto:"):
                continue
            tr = link.find_parent("tr")
            if not tr:
                continue
            cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
            if len(cells) < 3:
                continue
            location = cells[-2] if len(cells) >= 2 else ""
            posted_text = cells[-1] if len(cells) >= 1 else ""
            if not _looks_like_listing_title(title):
                continue
            rows.append(
                ParsedListingRow(
                    title=title,
                    location=location,
                    posted_text=posted_text,
                    href=urljoin(base_url, href),
                )
            )
        return rows

    rows: list[ParsedListingRow] = []
    for tr_match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        row_html = tr_match.group(1)
        td_values = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
        if len(td_values) < 3:
            continue
        link_match = re.search(r"<a[^>]*href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", td_values[0], flags=re.IGNORECASE | re.DOTALL)
        if not link_match:
            continue
        href = link_match.group(1).strip()
        title = re.sub(r"<[^>]+>", " ", link_match.group(2)).strip()
        location = re.sub(r"<[^>]+>", " ", td_values[-2]).strip()
        posted_text = re.sub(r"<[^>]+>", " ", td_values[-1]).strip()
        if not title or not href or href.startswith("mailto:") or not _looks_like_listing_title(title):
            continue
        rows.append(
            ParsedListingRow(
                title=re.sub(r"\s+", " ", title),
                location=re.sub(r"\s+", " ", location),
                posted_text=re.sub(r"\s+", " ", posted_text),
                href=urljoin(base_url, href),
            )
        )
    return rows


def _parse_posted_at(text: str) -> datetime | None:
    value = (text or "").strip()
    for fmt in ("%I:%M %p %d-%b-%y", "%H:%M %d-%b-%y"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _extract_external_post_id(source_url: str) -> str:
    parsed = urlparse(source_url or "")
    query = parse_qs(parsed.query)
    stable_id = (query.get("id") or [""])[0].strip()
    if stable_id:
        return f"nvoids:{stable_id}"
    tail = (parsed.path.rsplit("/", 1)[-1] or "").strip()
    return tail or (source_url or "")


def parse_external_post(
    *,
    source_type: str,
    source_url: str,
    title: str,
    location: str,
    posted_text: str,
    raw_body: str,
    raw_html: str,
    nvoids_detail: ParsedNvoidsDetail | None = None,
) -> ParsedExternalPost:
    canonical_title = _normalize_line(title)
    company = ""
    skills_text = ""
    if source_type == "nvoids" and _has_meaningful_html_text(raw_html):
        detail = nvoids_detail or parse_nvoids_detail(raw_html, canonical_title, location)
        canonical_title = detail.role or detail.listing_subject or canonical_title
        location = detail.location
        posted_text = detail.posted_text
        body = "\n".join(part for part in [detail.role, detail.location, detail.jd_body] if part).strip()
        recruiter_email = detail.recruiter_email
        recruiter_phone = detail.recruiter_phone
        recruiter_name = detail.recruiter_name
        parse_confidence = detail.parse_confidence
        company_match = re.search(
            r"(?im)^\s*(?:client|company)(?:\s*:\s*|\s+-\s+)([^\n,;]+)",
            detail.jd_body or detail.body,
        )
        company = company_match.group(1).strip() if company_match else ""
        skills_text = normalize_skills_text(extract_skills_text(detail.jd_body), preserve_unknown=True)
    else:
        body = f"{canonical_title}\n{location}\n{raw_body}".strip()
        emails = _EMAIL_RE.findall(body)
        recruiter_email = emails[0].lower() if emails else ""
        recruiter_phone = _extract_recruiter_phone(body)
        recruiter_name = ""
        parse_confidence = 0.7 if recruiter_email and recruiter_phone else (0.65 if recruiter_email else 0.45)
        skills_text = normalize_skills_text(extract_skills_text(body), preserve_unknown=True)
    lc = body.lower()
    work_mode = "Remote" if "remote" in lc else ("Hybrid" if "hybrid" in lc else ("Onsite" if "onsite" in lc else ""))
    visa_hints = "Mentioned" if any(token in lc for token in ("visa", "c2c", "w2", "1099", "opt", "h1b")) else ""
    duration_match = re.search(r"(?:duration|contract)\s*[:\-]\s*([^\n,;]+)", body, flags=re.IGNORECASE)
    duration = duration_match.group(1).strip() if duration_match else ""
    rate_match = re.search(r"(?:rate|max rate)\s*[:\-]?\s*([^\n;]+)", body, flags=re.IGNORECASE)
    rate = rate_match.group(1).strip() if rate_match else ""
    if not company:
        company_match = re.search(
            r"(?im)^\s*(?:client|company)(?:\s*:\s*|\s+-\s+)([^\n,;]+)",
            body,
        )
        company = company_match.group(1).strip() if company_match else ""
    external_post_id = _extract_external_post_id(source_url)
    return ParsedExternalPost(
        source_type=source_type,
        external_post_id=external_post_id,
        source_url=source_url,
        posted_at=_parse_posted_at(posted_text),
        role=canonical_title or title,
        location=location,
        work_mode=work_mode,
        recruiter_email=recruiter_email,
        recruiter_phone=recruiter_phone,
        recruiter_name=recruiter_name,
        company=company,
        visa_hints=visa_hints,
        duration=duration,
        rate=rate,
        skills_text=skills_text,
        raw_body=body,
        raw_html=raw_html,
        parse_confidence=parse_confidence,
    )


def parse_job_detail_contacts(detail_html: str) -> tuple[str, str, str]:
    if not (detail_html or "").strip():
        logger.info("nvoids_parse_contacts_skipped_empty_html")
        return "", "", ""
    detail = parse_nvoids_detail(detail_html, "", "")
    return detail.recruiter_email, detail.recruiter_phone, detail.recruiter_name


def _extract_recruiter_name(scoped_text: str) -> str:
    for raw_line in scoped_text.splitlines():
        line = raw_line.strip()
        match = _FROM_LINE_RE.match(line)
        if not match:
            continue
        candidate = match.group(1)
        candidate = _EMAIL_RE.sub("", candidate)
        candidate = re.split(r"\b(?:email|phone|mobile|contact|call|reach|time taken)\b", candidate, maxsplit=1, flags=re.IGNORECASE)[0]
        candidate = re.sub(r"[\(\)\[\]<>]", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip(" ,.-:")
        if len(candidate) < 2:
            continue
        if "reply to" in candidate.lower():
            continue
        return candidate
    return ""


def _extract_recruiter_phone(scoped_text: str) -> str:
    # Strict mode: only trust phones near explicit contact labels.
    label_match = _PHONE_LABEL_RE.search(scoped_text)
    if label_match:
        return re.sub(r"\s+", " ", label_match.group(1)).strip()

    # Secondary strict fallback: inspect a bounded signature window near "From:".
    from_match = re.search(r"\bfrom\s*:", scoped_text, flags=re.IGNORECASE)
    if from_match:
        start = from_match.start()
        signature_window = scoped_text[start : start + 350]
        if _NOISE_LINE_RE.search(signature_window):
            return ""
        candidate_match = _PHONE_RE.search(signature_window)
        if candidate_match:
            return re.sub(r"\s+", " ", candidate_match.group(0)).strip()
    return ""
