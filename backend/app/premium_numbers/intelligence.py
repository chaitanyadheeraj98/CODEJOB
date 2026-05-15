from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re

from sqlalchemy.orm import Session

from app.models import EmployerNumber, NumberReviewQueue, RecruiterEmail, RecruiterNumber, RecruiterOpportunity, UserSettings
from app.premium_numbers.extraction import ExtractedPhoneLead, extract_phone_leads

OPPORTUNITY_STATUS_VALUES = {"New", "Called", "Applied", "Follow Up", "Closed", "Not Interested"}


@dataclass(frozen=True)
class OpportunitySnapshot:
    recruiter_number_id: int
    source_email_id: int | None
    gmail_message_id: str
    email_subject: str
    email_sender: str
    gmail_open_url: str
    received_at: datetime | None
    job_title: str
    client: str
    location: str
    work_mode: str
    visa_restrictions: str
    extracted_skills: str
    evidence: str


def _employer_domains_for_owner(db: Session, owner_id: str) -> set[str]:
    settings_row = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    if not settings_row or not settings_row.employer_domains:
        return set()
    return {part.strip().lower() for part in settings_row.employer_domains.split(",") if part.strip()}


def _extract_job_metadata(subject: str, body: str) -> tuple[str, str, str, str, str, str]:
    content = f"{subject}\n{body}"
    content_l = content.lower()
    work_mode = "Remote" if "remote" in content_l else ("Hybrid" if "hybrid" in content_l else ("Onsite" if "onsite" in content_l else ""))
    visa_restrictions = "Mentioned" if any(token in content_l for token in ("visa", "c2c", "w2", "1099")) else ""
    client_match = re.search(r"(?:client|end client)\s*[:\-]\s*([^\n,;]+)", content, flags=re.IGNORECASE)
    client = (client_match.group(1).strip() if client_match else "")
    role_match = re.search(r"(?:role|position|title)\s*[:\-]\s*([^\n,;]+)", content, flags=re.IGNORECASE)
    job_title = role_match.group(1).strip() if role_match else subject.strip()
    skills: list[str] = []
    for token in ("java", "python", "node", "react", "aws", "sql", "graphql", "ai"):
        if token in content_l:
            skills.append(token.upper() if token in {"aws", "sql", "ai"} else token.title())
    extracted_skills = ", ".join(sorted(set(skills)))
    location_match = re.search(r"(?:location)\s*[:\-]\s*([^\n,;]+)", content, flags=re.IGNORECASE)
    location = (location_match.group(1).strip() if location_match else "")
    return job_title, client, location, work_mode, visa_restrictions, extracted_skills


def _build_snapshot(recruiter_number_id: int, email: RecruiterEmail, lead: ExtractedPhoneLead) -> OpportunitySnapshot:
    gmail_message_id = email.external_message_id or f"manual-{email.id}"
    job_title, client, location, work_mode, visa_restrictions, extracted_skills = _extract_job_metadata(email.subject or "", email.body or "")
    return OpportunitySnapshot(
        recruiter_number_id=recruiter_number_id,
        source_email_id=email.id,
        gmail_message_id=gmail_message_id,
        email_subject=email.subject or "",
        email_sender=email.sender or "",
        gmail_open_url=email.gmail_message_url or "",
        received_at=email.gmail_received_at or datetime.now(UTC),
        job_title=job_title,
        client=client,
        location=location,
        work_mode=work_mode,
        visa_restrictions=visa_restrictions,
        extracted_skills=extracted_skills,
        evidence=lead.source_fragment or "Extracted from email context",
    )


def _create_opportunity_if_new(db: Session, owner_id: str, snapshot: OpportunitySnapshot) -> None:
    existing = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            RecruiterOpportunity.recruiter_number_id == snapshot.recruiter_number_id,
            RecruiterOpportunity.gmail_message_id == snapshot.gmail_message_id,
        )
        .first()
    )
    if existing:
        return
    db.add(
        RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=snapshot.recruiter_number_id,
            source_email_id=snapshot.source_email_id,
            gmail_message_id=snapshot.gmail_message_id,
            email_subject=snapshot.email_subject,
            email_sender=snapshot.email_sender,
            gmail_open_url=snapshot.gmail_open_url,
            received_at=snapshot.received_at,
            job_title=snapshot.job_title,
            client=snapshot.client,
            location=snapshot.location,
            work_mode=snapshot.work_mode,
            visa_restrictions=snapshot.visa_restrictions,
            extracted_skills=snapshot.extracted_skills,
            evidence=snapshot.evidence,
            status="New",
            notes="",
        )
    )


def process_email_number_intelligence(db: Session, email: RecruiterEmail) -> None:
    employer_domains = _employer_domains_for_owner(db, email.owner_id)
    leads = extract_phone_leads(email.sender or "", email.subject or "", email.body or "", employer_domains=employer_domains)
    for lead in leads:
        recruiter_number = (
            db.query(RecruiterNumber)
            .filter(
                RecruiterNumber.owner_id == email.owner_id,
                RecruiterNumber.normalized_phone_number == lead.phone_number_normalized,
            )
            .first()
        )
        if recruiter_number:
            snapshot = _build_snapshot(recruiter_number.id, email, lead)
            _create_opportunity_if_new(db, email.owner_id, snapshot)
            continue

        employer_number = (
            db.query(EmployerNumber)
            .filter(
                EmployerNumber.owner_id == email.owner_id,
                EmployerNumber.normalized_phone_number == lead.phone_number_normalized,
            )
            .first()
        )
        if employer_number:
            continue

        existing_review = (
            db.query(NumberReviewQueue)
            .filter(
                NumberReviewQueue.owner_id == email.owner_id,
                NumberReviewQueue.normalized_phone_number == lead.phone_number_normalized,
                NumberReviewQueue.source_email_id == email.id,
            )
            .first()
        )
        if existing_review:
            continue
        db.add(
            NumberReviewQueue(
                owner_id=email.owner_id,
                source_email_id=email.id,
                normalized_phone_number=lead.phone_number_normalized,
                display_phone_number=lead.phone_number_display,
                owner_name=lead.owner_name,
                company=lead.company,
                designation=lead.designation,
                confidence=lead.confidence,
                purpose=lead.purpose,
                evidence_snippet=lead.source_fragment,
                email_subject=email.subject or "",
                email_sender=email.sender or "",
                gmail_open_url=email.gmail_message_url or "",
                state="pending",
            )
        )
