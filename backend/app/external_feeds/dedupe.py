from __future__ import annotations

import hashlib
import re
from datetime import datetime


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _norm_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def build_dedupe_hash(*, recruiter_phone: str, recruiter_email: str, role: str, location: str, posted_at: datetime | None, raw_body: str) -> str:
    date_bucket = posted_at.strftime("%Y-%m-%d") if posted_at else "unknown-date"
    body_sig = hashlib.sha1(_norm(raw_body).encode("utf-8", errors="ignore")).hexdigest()[:12]
    key = "|".join(
        [
            _norm_phone(recruiter_phone),
            _norm(recruiter_email),
            _norm(role),
            _norm(location),
            date_bucket,
            body_sig,
        ]
    )
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
