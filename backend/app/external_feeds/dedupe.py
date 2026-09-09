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


def build_email_content_hash(*, sender: str, subject: str, body: str) -> str:
    """Fingerprint for gmail/nvoids rows, so a recruiter's system re-sending the exact
    same message under a new external_message_id (a real, observed failure mode -
    not a hypothetical) is recognised even though its delivery identity differs.

    No date bucket, unlike build_dedupe_hash: a resend next month is still the same
    requirement worth catching, and there's no re-paste-by-hand case here to protect
    against as there is for manual intake.
    """
    key = "|".join([_norm(sender), _norm(subject), _norm(body)])
    return hashlib.sha1(key.encode("utf-8", errors="ignore")).hexdigest()
