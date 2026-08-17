from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models import EmployerNumber, NumberReviewQueue, PremiumNumberLead, RecruiterEmail, RecruiterNumber, RecruiterOpportunity
from app.phase0 import email_domain
from app.premium_numbers.domain_guard import should_capture_premium_numbers
from app.premium_numbers.extraction import ExtractedPhoneLead, extract_phone_leads

logger = logging.getLogger(__name__)
TARGET_CONTACT_SIGNAL_RE = re.compile(r"\b(?:share|send|submit|mail|email)[\s\S]{0,120}\bto\b", re.IGNORECASE)
TARGET_CONTACT_INTENT_RE = re.compile(r"\b(?:share|send|submit|mail|email|contact|reach|call)\b", re.IGNORECASE)
EMAIL_LOCAL_PART_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]*$")


def derive_name_from_contact_email(contact_email: str | None) -> str:
    email = (contact_email or "").strip().lower()
    if "@" not in email:
        return ""
    local_part = email.split("@", 1)[0].strip()
    if not local_part or not EMAIL_LOCAL_PART_RE.match(local_part):
        return ""
    clean = re.sub(r"[._-]+", " ", local_part).strip()
    if not clean:
        return ""
    parts = [part for part in clean.split() if part]
    if not parts:
        return ""
    return " ".join(part.capitalize() for part in parts)


def derive_company_from_email_domain(email: str | None) -> str:
    domain = email_domain(email or "")
    base = domain.split(".", 1)[0].strip() if domain else ""
    return base.title() if base else ""


@dataclass(frozen=True)
class PhoneIntelligenceWorkflowResult:
    source: str
    processed_numbers: int
    stored_count: int
    review_created: int
    review_existing: int
    recruiter_matches: int
    employer_matches: int
    opportunity_created: int
    opportunity_existing: int
    skipped_by_domain_guard: bool


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


@dataclass(frozen=True)
class _IdempotencyPoint:
    recruiter_number: RecruiterNumber | None
    employer_number: EmployerNumber | None
    existing_review: NumberReviewQueue | None
    existing_opportunity: RecruiterOpportunity | None


class PhoneIntelligenceWorkflowService:
    def __init__(self, *, manage_transaction: bool = True):
        self.manage_transaction = manage_transaction

    def process_email(self, db: Session, email: RecruiterEmail, *, source: str) -> PhoneIntelligenceWorkflowResult:
        return self._run(db, email, source=source, include_premium_lead_upsert=True, include_intelligence=True)

    def extract_only(self, db: Session, email: RecruiterEmail, *, source: str) -> int:
        result = self._run(db, email, source=source, include_premium_lead_upsert=True, include_intelligence=False)
        return result.stored_count

    def classify_only(self, db: Session, email: RecruiterEmail, *, source: str) -> PhoneIntelligenceWorkflowResult:
        return self._run(db, email, source=source, include_premium_lead_upsert=False, include_intelligence=True)

    def _run(
        self,
        db: Session,
        email: RecruiterEmail,
        *,
        source: str,
        include_premium_lead_upsert: bool,
        include_intelligence: bool,
    ) -> PhoneIntelligenceWorkflowResult:
        try:
            leads = self._extract_leads(db, email)
            if leads is None:
                result = PhoneIntelligenceWorkflowResult(
                    source=source,
                    processed_numbers=0,
                    stored_count=0,
                    review_created=0,
                    review_existing=0,
                    recruiter_matches=0,
                    employer_matches=0,
                    opportunity_created=0,
                    opportunity_existing=0,
                    skipped_by_domain_guard=True,
                )
                self._finalize(db)
                return result

            stored_count = 0
            review_created = 0
            review_existing = 0
            recruiter_matches = 0
            employer_matches = 0
            opportunity_created = 0
            opportunity_existing = 0

            for lead in leads:
                if include_premium_lead_upsert:
                    stored_count += self._upsert_premium_lead(db, email, lead)

                if not include_intelligence:
                    continue

                idempotency = self._idempotency_point(db, email, lead)
                if idempotency.recruiter_number:
                    self._enrich_existing_recruiter_number(idempotency.recruiter_number, lead)
                    recruiter_matches += 1
                    if idempotency.existing_opportunity:
                        opportunity_existing += 1
                    else:
                        snapshot = self._build_snapshot(idempotency.recruiter_number.id, email, lead)
                        self._create_opportunity(db, email.owner_id, snapshot)
                        opportunity_created += 1
                    continue

                if idempotency.employer_number:
                    employer_matches += 1
                    continue

                if idempotency.existing_review:
                    review_existing += 1
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
                        email_sender=lead.contact_email or email.sender or "",
                        gmail_open_url=email.gmail_message_url or "",
                        state="pending",
                    )
                )
                review_created += 1

            self._finalize(db)
            return PhoneIntelligenceWorkflowResult(
                source=source,
                processed_numbers=len(leads),
                stored_count=stored_count,
                review_created=review_created,
                review_existing=review_existing,
                recruiter_matches=recruiter_matches,
                employer_matches=employer_matches,
                opportunity_created=opportunity_created,
                opportunity_existing=opportunity_existing,
                skipped_by_domain_guard=False,
            )
        except Exception:
            self._rollback(db)
            raise

    def _enrich_existing_recruiter_number(self, recruiter: RecruiterNumber, lead: ExtractedPhoneLead) -> None:
        name = (recruiter.recruiter_name or "").strip().lower()
        lead_name = (lead.owner_name or "").strip()
        existing_name = (recruiter.recruiter_name or "").strip()
        normalized_lead_name = lead_name.lower()
        derived_contact_name = self._derive_name_from_contact_email(lead.contact_email)

        replacement_name = ""
        if lead_name and normalized_lead_name != "unknown":
            replacement_name = lead_name
        elif derived_contact_name:
            replacement_name = derived_contact_name

        if replacement_name and name in {"", "unknown"}:
            recruiter.recruiter_name = replacement_name
        elif self._is_strong_target_contact_signal(lead):
            candidate_names = [n for n in [replacement_name, derived_contact_name] if n]
            for candidate in candidate_names:
                if existing_name and existing_name.lower() != candidate.lower():
                    recruiter.recruiter_name = candidate
                    break

        designation = (recruiter.designation or "").strip().lower()
        lead_designation = (lead.designation or "").strip()
        if lead_designation and lead_designation.lower() != "unknown" and designation in {"", "unknown"}:
            recruiter.designation = lead_designation

        company = (recruiter.company or "").strip().lower()
        lead_company = (lead.company or "").strip()
        if lead_company and lead_company.lower() != "unknown" and company in {"", "unknown"}:
            recruiter.company = lead_company

    def _is_strong_target_contact_signal(self, lead: ExtractedPhoneLead) -> bool:
        fragment = (lead.source_fragment or "").strip()
        if not fragment:
            return False

        if TARGET_CONTACT_SIGNAL_RE.search(fragment):
            return True

        fragment_lower = fragment.lower()
        has_contact_intent = bool(TARGET_CONTACT_INTENT_RE.search(fragment))
        has_mailto = "mailto:" in fragment_lower

        normalized_digits = re.sub(r"\D", "", lead.phone_number_normalized or "")
        display_digits = re.sub(r"\D", "", lead.phone_number_display or "")
        has_phone_digits = (
            (normalized_digits and normalized_digits in re.sub(r"\D", "", fragment))
            or (display_digits and display_digits in re.sub(r"\D", "", fragment))
        )

        contact_email = (lead.contact_email or "").strip().lower()
        has_contact_email = bool(contact_email and contact_email in fragment_lower)
        has_owner_name = bool((lead.owner_name or "").strip() and (lead.owner_name or "").strip().lower() != "unknown")

        return has_owner_name and has_phone_digits and (has_contact_email or has_mailto or has_contact_intent)

    def _derive_name_from_contact_email(self, contact_email: str | None) -> str:
        return derive_name_from_contact_email(contact_email)

    def _extract_leads(self, db: Session, email: RecruiterEmail) -> list[ExtractedPhoneLead] | None:
        allowed, sender_domain, configured_domains = should_capture_premium_numbers(db, email)
        if not allowed:
            logger.debug(
                "Skipping phone intelligence workflow for email_id=%s sender_domain=%s allowed_domains=%s",
                email.id,
                sender_domain or "<none>",
                configured_domains,
            )
            return None
        employer_domains = {part.strip() for part in configured_domains.split(",") if part.strip()}
        return extract_phone_leads(
            email.sender or "",
            email.subject or "",
            email.body or "",
            employer_domains=employer_domains,
        )

    def _upsert_premium_lead(self, db: Session, email: RecruiterEmail, lead: ExtractedPhoneLead) -> int:
        existing = (
            db.query(PremiumNumberLead)
            .filter(
                PremiumNumberLead.owner_id == email.owner_id,
                PremiumNumberLead.recruiter_email_id == email.id,
                PremiumNumberLead.phone_number_normalized == lead.phone_number_normalized,
            )
            .first()
        )
        if existing:
            existing.phone_number_display = lead.phone_number_display
            existing.owner_name = lead.owner_name
            existing.company = lead.company
            existing.designation = lead.designation
            existing.purpose = lead.purpose
            existing.confidence = lead.confidence
            existing.contact_type = lead.contact_type
            existing.recruiter_relevance_score = lead.recruiter_relevance_score
            existing.is_recruiter_relevant = lead.is_recruiter_relevant
            existing.relevance_reason = lead.relevance_reason
            existing.source_fragment = lead.source_fragment
            existing.source_email_sender = email.sender
            existing.source_email_subject = email.subject
            existing.source_email_message_id = email.external_message_id
            return 1

        db.add(
            PremiumNumberLead(
                owner_id=email.owner_id,
                recruiter_email_id=email.id,
                phone_number_normalized=lead.phone_number_normalized,
                phone_number_display=lead.phone_number_display,
                owner_name=lead.owner_name,
                company=lead.company,
                designation=lead.designation,
                purpose=lead.purpose,
                confidence=lead.confidence,
                contact_type=lead.contact_type,
                recruiter_relevance_score=lead.recruiter_relevance_score,
                is_recruiter_relevant=lead.is_recruiter_relevant,
                relevance_reason=lead.relevance_reason,
                source_fragment=lead.source_fragment,
                source_email_sender=email.sender,
                source_email_subject=email.subject,
                source_email_message_id=email.external_message_id,
            )
        )
        return 1

    def _idempotency_point(self, db: Session, email: RecruiterEmail, lead: ExtractedPhoneLead) -> _IdempotencyPoint:
        recruiter_number = (
            db.query(RecruiterNumber)
            .filter(
                RecruiterNumber.owner_id == email.owner_id,
                RecruiterNumber.normalized_phone_number == lead.phone_number_normalized,
            )
            .first()
        )
        employer_number = (
            db.query(EmployerNumber)
            .filter(
                EmployerNumber.owner_id == email.owner_id,
                EmployerNumber.normalized_phone_number == lead.phone_number_normalized,
            )
            .first()
        )
        existing_review = (
            db.query(NumberReviewQueue)
            .filter(
                NumberReviewQueue.owner_id == email.owner_id,
                NumberReviewQueue.normalized_phone_number == lead.phone_number_normalized,
                NumberReviewQueue.source_email_id == email.id,
            )
            .first()
        )
        existing_opportunity = None
        if recruiter_number:
            gmail_message_id = email.external_message_id or f"manual-{email.id}"
            existing_opportunity = (
                db.query(RecruiterOpportunity)
                .filter(
                    RecruiterOpportunity.owner_id == email.owner_id,
                    RecruiterOpportunity.recruiter_number_id == recruiter_number.id,
                    RecruiterOpportunity.gmail_message_id == gmail_message_id,
                )
                .first()
            )
        return _IdempotencyPoint(
            recruiter_number=recruiter_number,
            employer_number=employer_number,
            existing_review=existing_review,
            existing_opportunity=existing_opportunity,
        )

    def _extract_job_metadata(self, subject: str, body: str) -> tuple[str, str, str, str, str, str]:
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

    def _build_snapshot(self, recruiter_number_id: int, email: RecruiterEmail, lead: ExtractedPhoneLead) -> OpportunitySnapshot:
        gmail_message_id = email.external_message_id or f"manual-{email.id}"
        job_title, client, location, work_mode, visa_restrictions, extracted_skills = self._extract_job_metadata(email.subject or "", email.body or "")
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

    def _create_opportunity(self, db: Session, owner_id: str, snapshot: OpportunitySnapshot) -> None:
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

    def _finalize(self, db: Session) -> None:
        if self.manage_transaction:
            db.commit()
        else:
            db.flush()

    def _rollback(self, db: Session) -> None:
        if self.manage_transaction:
            db.rollback()
