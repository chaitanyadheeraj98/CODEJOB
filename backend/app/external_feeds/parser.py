from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse

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


def _normalize_line(value: str) -> str:
    line = re.sub(r"\s+", " ", str(value or "")).strip()
    return line.strip(" |:-")


def _is_meaningful_nvoids_title_line(line: str) -> bool:
    normalized = _normalize_line(line)
    if not normalized:
        return False
    if _SKIP_DETAIL_TITLE_RE.search(normalized):
        return False
    if _NOISE_LINE_RE.search(normalized) or _HTML_ROLE_NOISE_RE.search(normalized):
        return False
    if _EMAIL_RE.search(normalized) or normalized.lower().startswith("http"):
        return False
    return True


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


def _extract_title_location_from_subject(subject: str) -> tuple[str, str]:
    normalized = _normalize_line(subject)
    if not normalized:
        return "", ""
    if " at " in normalized.lower():
        parts = re.split(r"\s+at\s+", normalized, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            return _normalize_line(parts[0]), _normalize_line(parts[1])
    if " -- " in normalized:
        title_part, location_part = normalized.split(" -- ", 1)
        return _normalize_line(title_part), _normalize_line(location_part)
    return normalized, ""


def _extract_labeled_value(lines: list[str], pattern: re.Pattern[str]) -> str:
    for index, raw_line in enumerate(lines):
        raw = str(raw_line or "").strip()
        line = _normalize_line(raw_line)
        if not raw and not line:
            continue
        match = pattern.match(raw) or pattern.match(line)
        if match:
            value = _normalize_line(match.group(1))
            if value:
                return value
            if index + 1 < len(lines):
                next_line = _normalize_line(lines[index + 1])
                if next_line and not next_line.endswith(":"):
                    return next_line
        if raw.rstrip().endswith(":") and pattern.match(raw.rstrip()[:-1] + ":"):
            if index + 1 < len(lines):
                next_line = _normalize_line(lines[index + 1])
                if next_line:
                    return next_line
    return ""


def _extract_recruiter_name_from_lines(lines: list[str]) -> str:
    for index, raw_line in enumerate(lines):
        line = _normalize_line(raw_line)
        if not line:
            continue
        match = _FROM_LINE_RE.match(line)
        if match:
            candidate = _normalize_line(match.group(1))
            if candidate:
                return candidate
            if index + 1 < len(lines):
                next_line = _normalize_line(lines[index + 1])
                if next_line and not _GENERIC_ROW_NOISE_RE.search(next_line):
                    return next_line
        if line.lower() == "from" and index + 1 < len(lines):
            next_line = _normalize_line(lines[index + 1])
            if next_line and not _GENERIC_ROW_NOISE_RE.search(next_line):
                return next_line
    return ""


def _extract_recruiter_phone_from_fragments(row_texts: list[str], row_htmls: list[str]) -> str:
    for row_text, row_html in zip(row_texts, row_htmls):
        combined = f"{row_text}\n{row_html}"
        phone = _extract_recruiter_phone(combined)
        if phone:
            return phone
    return ""


def _build_clean_body_from_lines(lines: list[str], *, listing_subject: str, recruiter_email: str, posted_text: str) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    subject_title, subject_location = _extract_title_location_from_subject(listing_subject)
    for raw_line in lines:
        line = _normalize_line(raw_line)
        if not line:
            continue
        lower = line.lower()
        if _GENERIC_ROW_NOISE_RE.search(line) or _NOISE_LINE_RE.search(line) or _HTML_ROLE_NOISE_RE.search(line):
            continue
        if lower == "home" or lower.startswith("email:") or lower.startswith("reply to"):
            continue
        if lower.startswith("http://") or lower.startswith("https://") or lower.startswith("www."):
            continue
        if lower.startswith("view all") or lower.startswith("posts from recruiter"):
            continue
        if line == listing_subject or line == subject_title or line == subject_location:
            continue
        if recruiter_email and recruiter_email in lower:
            continue
        if posted_text and line == posted_text:
            continue
        if lower.startswith("thanks") or lower.startswith("regards"):
            continue
        if line in seen:
            continue
        seen.add(line)
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _extract_nvoids_table_rows(detail_html: str) -> tuple[list[str], list[str]]:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(detail_html or "", "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        best_rows: list[str] = []
        best_html_rows: list[str] = []
        best_score = -999
        for table in soup.find_all("table"):
            row_texts: list[str] = []
            row_htmls: list[str] = []
            for row in table.find_all("tr"):
                cells = row.find_all(["td", "th"])
                text = _normalize_line(" ".join(cell.get_text(" ", strip=True) for cell in cells) if cells else row.get_text(" ", strip=True))
                html = "".join(str(cell) for cell in cells) if cells else str(row)
                if text:
                    row_texts.append(text)
                    row_htmls.append(html)
            if not row_texts:
                continue
            joined = "\n".join(row_texts).lower()
            score = 0
            if "home" in joined:
                score -= 1
            if any(text.lower().startswith("email:") for text in row_texts):
                score += 5
            if any(_POSTED_TEXT_RE.search(text) for text in row_texts):
                score += 3
            if any("view all" in text.lower() for text in row_texts):
                score += 2
            if any(_DETAIL_LINK_RE.search(text) for text in row_texts):
                score += 2
            if any(("job title" in text.lower()) or ("job role" in text.lower()) or ("location:" in text.lower()) for text in row_texts):
                score += 3
            if any("search results" in text.lower() or "a collection of search strings" in text.lower() for text in row_texts):
                score -= 8
            if len(row_texts) >= 4:
                score += 1
            if score > best_score:
                best_score = score
                best_rows = row_texts
                best_html_rows = row_htmls
        return best_rows, best_html_rows

    segments = re.findall(r"<tr[^>]*>(.*?)</tr>", detail_html or "", flags=re.IGNORECASE | re.DOTALL)
    row_texts: list[str] = []
    row_htmls: list[str] = []
    for segment in segments:
        text = re.sub(r"<[^>]+>", " ", segment)
        text = _normalize_line(text)
        if text:
            row_texts.append(text)
            row_htmls.append(segment)
    return row_texts, row_htmls


def _fallback_nvoids_detail(fallback_title: str, fallback_location: str) -> ParsedNvoidsDetail:
    normalized_title = _normalize_line(fallback_title)
    normalized_location = _normalize_line(fallback_location)
    return ParsedNvoidsDetail(
        listing_subject=normalized_title,
        recruiter_email="",
        recruiter_phone="",
        recruiter_name="",
        body="",
        repeated_email="",
        posted_text="",
        role=normalized_title,
        location=normalized_location,
        raw_table_text="",
        parse_confidence=0.15,
    )


def parse_nvoids_detail(detail_html: str, fallback_title: str, fallback_location: str) -> ParsedNvoidsDetail:
    if not (detail_html or "").strip():
        logger.info("nvoids_parse_detail_skipped_empty_html fallback_title=%r", fallback_title)
        return _fallback_nvoids_detail(fallback_title, fallback_location)
    row_texts, row_htmls = _extract_nvoids_table_rows(detail_html)
    listing_subject = ""
    posted_text = ""
    recruiter_email = ""
    repeated_email = ""

    for row_text, row_html in zip(row_texts, row_htmls):
        if not listing_subject and _is_meaningful_nvoids_title_line(row_text) and _looks_like_listing_title(row_text):
            listing_subject = row_text
        if not recruiter_email and row_text.lower().startswith("email:"):
            emails = _extract_emails_from_fragment(row_html, row_text)
            if emails:
                recruiter_email = emails[0]
        if not posted_text and _POSTED_TEXT_RE.search(row_text):
            posted_match = _POSTED_TEXT_RE.search(row_text)
            if posted_match:
                posted_text = _normalize_line(posted_match.group(0))
        if not repeated_email and "view all" in row_text.lower():
            emails = _extract_emails_from_fragment(row_html, row_text)
            if emails:
                repeated_email = emails[0]

    if not listing_subject:
        listing_subject = extract_nvoids_detail_title(detail_html, fallback_title)
    if not recruiter_email:
        for row_text, row_html in zip(row_texts, row_htmls):
            emails = _extract_emails_from_fragment(row_html, row_text)
            if emails:
                recruiter_email = emails[0]
                if row_text.lower().startswith("email:"):
                    break
                if "view all" in row_text.lower():
                    repeated_email = emails[0]
                    break
    if not repeated_email:
        repeated_email = recruiter_email

    recruiter_name = _extract_recruiter_name_from_lines(row_texts)
    recruiter_phone = _extract_recruiter_phone_from_fragments(row_texts, row_htmls)

    role = _extract_labeled_value(row_texts, _ROLE_LABEL_RE)
    location = _extract_labeled_value(row_texts, _LOCATION_LABEL_RE)
    _title_from_subject, location_from_subject = _extract_title_location_from_subject(listing_subject)
    if not role:
        role = listing_subject or _normalize_line(fallback_title)
    if not location:
        location = location_from_subject or _normalize_line(fallback_location)

    body = _build_clean_body_from_lines(
        row_texts,
        listing_subject=listing_subject,
        recruiter_email=recruiter_email,
        posted_text=posted_text,
    )
    if not body:
        body = _extract_detail_scope_text(detail_html)

    confidence = 0.35
    if row_texts:
        confidence += 0.15
    if listing_subject:
        confidence += 0.15
    if recruiter_email:
        confidence += 0.15
    if body:
        confidence += 0.1
    if role and role != _normalize_line(fallback_title):
        confidence += 0.05
    if location:
        confidence += 0.05
    if posted_text:
        confidence += 0.05

    raw_table_text = "\n".join(row_texts).strip() if row_texts else _extract_detail_scope_text(detail_html)
    return ParsedNvoidsDetail(
        listing_subject=listing_subject or _normalize_line(fallback_title),
        recruiter_email=recruiter_email,
        recruiter_phone=recruiter_phone,
        recruiter_name=recruiter_name,
        body=body.strip(),
        repeated_email=repeated_email,
        posted_text=posted_text,
        role=role,
        location=location,
        raw_table_text=raw_table_text,
        parse_confidence=min(confidence, 0.98),
    )


def extract_nvoids_detail_title(detail_html: str, fallback_title: str) -> str:
    row_texts, _row_htmls = _extract_nvoids_table_rows(detail_html)
    for row_text in row_texts:
        normalized = _normalize_line(row_text)
        if _is_meaningful_nvoids_title_line(normalized) and _looks_like_listing_title(normalized):
            return normalized
    for row_text in row_texts:
        normalized = _normalize_line(row_text)
        if _is_meaningful_nvoids_title_line(normalized):
            return normalized
    return _normalize_line(fallback_title) or fallback_title


def extract_nvoids_clean_body(detail_html: str, fallback_title: str, location: str) -> str:
    detail = parse_nvoids_detail(detail_html, fallback_title, location)
    parts = [detail.role]
    if detail.location and detail.location.lower() not in detail.role.lower():
        parts.append(detail.location)
    if detail.body:
        parts.append(detail.body)
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


def parse_external_post(*, source_type: str, source_url: str, title: str, location: str, posted_text: str, raw_body: str, raw_html: str) -> ParsedExternalPost:
    canonical_title = _normalize_line(title)
    if source_type == "nvoids" and _has_meaningful_html_text(raw_html):
        detail = parse_nvoids_detail(raw_html, canonical_title, location)
        canonical_title = detail.role or detail.listing_subject or canonical_title
        location = detail.location or location
        posted_text = detail.posted_text or posted_text
        body = "\n".join(part for part in [detail.role, detail.location, detail.body] if part).strip()
        recruiter_email = detail.recruiter_email
        recruiter_phone = detail.recruiter_phone
        recruiter_name = detail.recruiter_name
        parse_confidence = detail.parse_confidence
    else:
        body = f"{canonical_title}\n{location}\n{raw_body}".strip()
        emails = _EMAIL_RE.findall(body)
        recruiter_email = emails[0].lower() if emails else ""
        recruiter_phone = _extract_recruiter_phone(body)
        recruiter_name = ""
        parse_confidence = 0.7 if recruiter_email and recruiter_phone else (0.65 if recruiter_email else 0.45)
    lc = body.lower()
    work_mode = "Remote" if "remote" in lc else ("Hybrid" if "hybrid" in lc else ("Onsite" if "onsite" in lc else ""))
    visa_hints = "Mentioned" if any(token in lc for token in ("visa", "c2c", "w2", "1099", "opt", "h1b")) else ""
    duration_match = re.search(r"(?:duration|contract)\s*[:\-]\s*([^\n,;]+)", body, flags=re.IGNORECASE)
    duration = duration_match.group(1).strip() if duration_match else ""
    rate_match = re.search(r"(?:rate|max rate)\s*[:\-]?\s*([^\n;]+)", body, flags=re.IGNORECASE)
    rate = rate_match.group(1).strip() if rate_match else ""
    company_match = re.search(r"(?:client|company)\s*[:\-]\s*([^\n,;]+)", body, flags=re.IGNORECASE)
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
        skills_text=normalize_skills_text(extract_skills_text(body), preserve_unknown=True),
        raw_body=body,
        raw_html=raw_html,
        parse_confidence=parse_confidence,
    )


def parse_job_detail_contacts(detail_html: str) -> tuple[str, str, str]:
    if not (detail_html or "").strip():
        logger.info("nvoids_parse_contacts_skipped_empty_html")
        return "", "", ""
    detail = parse_nvoids_detail(detail_html, "", "")
    recruiter_email = detail.recruiter_email
    recruiter_phone = detail.recruiter_phone
    recruiter_name = detail.recruiter_name
    if recruiter_email or recruiter_phone or recruiter_name:
        return recruiter_email, recruiter_phone, recruiter_name
    scoped_text = _extract_detail_scope_text(detail_html)
    lines = [line.strip() for line in scoped_text.splitlines() if line.strip()]
    recruiter_email = _extract_first_email(lines)
    recruiter_name = _extract_recruiter_name(scoped_text)
    recruiter_phone = _extract_recruiter_phone(scoped_text)
    return recruiter_email, recruiter_phone, recruiter_name


def _extract_detail_scope_text(detail_html: str) -> str:
    if BeautifulSoup is not None:
        soup = BeautifulSoup(detail_html or "", "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        scored_blocks: list[tuple[int, str]] = []
        for cell in soup.find_all("td"):
            text = cell.get_text("\n", strip=True)
            if not text:
                continue
            low = text.lower()
            score = 0
            if "email:" in low:
                score += 4
            if "from:" in low or "reply to:" in low:
                score += 4
            if "hiring" in low or "location" in low or "duration" in low:
                score += 1
            if _NOISE_LINE_RE.search(low):
                score -= 3
            if score > 0:
                scored_blocks.append((score, text))
        if scored_blocks:
            scored_blocks.sort(key=lambda item: item[0], reverse=True)
            selected = [block for _, block in scored_blocks[:8]]
            return "\n".join(selected)
        return soup.get_text("\n", strip=True)
    text = re.sub(r"(?i)</(td|tr|div|p|br|li|h1|h2|h3|h4|h5|h6)>", "\n", detail_html or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _extract_first_email(lines: list[str]) -> str:
    for line in lines:
        matches = _EMAIL_RE.findall(line)
        if matches:
            return matches[0].lower()
    return ""


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
