from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ParsedExternalPost:
    source_type: str
    external_post_id: str
    source_url: str
    posted_at: datetime | None
    role: str
    location: str
    work_mode: str
    recruiter_email: str
    recruiter_phone: str
    recruiter_name: str
    company: str
    visa_hints: str
    duration: str
    rate: str
    skills_text: str
    raw_body: str
    raw_html: str
    parse_confidence: float


@dataclass(frozen=True)
class ExternalFeedSyncResult:
    source_type: str
    fetched_count: int
    created_count: int
    deduped_count: int
    failed_count: int
    run_id: int


@dataclass(frozen=True)
class ParsedListingRow:
    title: str
    location: str
    posted_text: str
    href: str
