from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import UserSettings
from app.phase0 import normalize_employer_domains


def employer_domains_for_owner(db: Session, owner_id: str) -> set[str]:
    settings_row = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    raw_domains: list[str] = []
    if settings_row and settings_row.employer_domains:
        raw_domains = [part.strip() for part in settings_row.employer_domains.split(",") if part.strip()]
    return normalize_employer_domains(raw_domains)
