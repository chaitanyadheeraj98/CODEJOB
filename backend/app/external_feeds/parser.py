from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urljoin, urlparse

from app.skill_taxonomy import normalize_skills_text

try:
    from bs4 import BeautifulSoup
except ModuleNotFoundError:  # pragma: no cover - fallback path for minimal envs
    BeautifulSoup = None

from .types import ParsedExternalPost, ParsedListingRow

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
        if not title or not href or href.startswith("mailto:"):
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
    body = f"{title}\n{location}\n{raw_body}".strip()
    emails = _EMAIL_RE.findall(body)
    recruiter_phone = _extract_recruiter_phone(body)
    lc = body.lower()
    work_mode = "Remote" if "remote" in lc else ("Hybrid" if "hybrid" in lc else ("Onsite" if "onsite" in lc else ""))
    visa_hints = "Mentioned" if any(token in lc for token in ("visa", "c2c", "w2", "1099", "opt", "h1b")) else ""
    duration_match = re.search(r"(?:duration|contract)\s*[:\-]\s*([^\n,;]+)", body, flags=re.IGNORECASE)
    duration = duration_match.group(1).strip() if duration_match else ""
    rate_match = re.search(r"(?:rate|max rate)\s*[:\-]?\s*([^\n;]+)", body, flags=re.IGNORECASE)
    rate = rate_match.group(1).strip() if rate_match else ""
    company_match = re.search(r"(?:client|company)\s*[:\-]\s*([^\n,;]+)", body, flags=re.IGNORECASE)
    company = company_match.group(1).strip() if company_match else ""
    recruiter_name = ""
    skills = []
    for token in ("java", "python", "react", "node", "aws", "sql", "azure", "sap", "salesforce", "ai", "ml"):
        if token in lc:
            skills.append(token.upper() if token in {"aws", "sql", "ai", "ml", "sap"} else token.title())

    external_post_id = _extract_external_post_id(source_url)
    return ParsedExternalPost(
        source_type=source_type,
        external_post_id=external_post_id,
        source_url=source_url,
        posted_at=_parse_posted_at(posted_text),
        role=title,
        location=location,
        work_mode=work_mode,
        recruiter_email=(emails[0] if emails else ""),
        recruiter_phone=recruiter_phone,
        recruiter_name=recruiter_name,
        company=company,
        visa_hints=visa_hints,
        duration=duration,
        rate=rate,
        skills_text=normalize_skills_text(", ".join(sorted(set(skills))), preserve_unknown=True),
        raw_body=body,
        raw_html=raw_html,
        parse_confidence=0.7 if emails and recruiter_phone else (0.65 if emails else 0.45),
    )


def parse_job_detail_contacts(detail_html: str) -> tuple[str, str, str]:
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
