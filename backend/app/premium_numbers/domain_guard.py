from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import PremiumNumberContact, UserSettings
from app.premium_numbers.phone_normalization import canonicalize_phone
from app.phase0 import email_domain, normalize_employer_domains

PERSONAL_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "hotmail.com",
        "icloud.com",
        "aol.com",
        "protonmail.com",
        "live.com",
        "msn.com",
        "ymail.com",
        "rediffmail.com",
        "mail.com",
        "gmx.com",
    }
)


def employer_domains_for_owner(db: Session, owner_id: str) -> set[str]:
    settings_row = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    raw_domains: list[str] = []
    if settings_row and settings_row.employer_domains:
        raw_domains = [part.strip() for part in settings_row.employer_domains.split(",") if part.strip()]
    return normalize_employer_domains(raw_domains)


def is_derivable_company_domain(db: Session, owner_id: str, email: str | None) -> bool:
    domain = email_domain(email or "")
    if not domain:
        return False
    if domain in PERSONAL_EMAIL_DOMAINS:
        return False
    return domain not in employer_domains_for_owner(db, owner_id)


def is_hidden_nvoids_placeholder_recruiter(row: PremiumNumberContact | None) -> bool:
    if row is None:
        return False
    normalized = str(row.normalized_phone_number or "").strip().lower()
    display = str(row.display_phone_number or "").strip().lower()
    return normalized.startswith("nvoids-") and display == "unknown" and row.first_detected_email_id is None


def is_hidden_invalid_employer_number(row: PremiumNumberContact | None) -> bool:
    if row is None:
        return False
    display = str(row.display_phone_number or "").strip()
    if not display or display.lower() == "unknown":
        return False
    normalized = str(row.normalized_phone_number or "").strip()
    return not canonicalize_phone(normalized) and not canonicalize_phone(display)
