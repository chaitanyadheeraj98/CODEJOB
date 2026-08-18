from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.external_feeds.models import ExternalOpportunity
from app.models import (
    NumberReviewQueue,
    PremiumNumberContact,
    PremiumNumberLead,
    RecruiterEmail,
    RecruiterOpportunity,
)
from app.phase0 import email_domain
from app.premium_numbers.domain_guard import employer_domains_for_owner
from app.premium_numbers.extraction import ExtractedContactGroup, extract_phone_leads


EMAIL_LOCAL_PART_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9._-]*$")


def derive_name_from_contact_email(contact_email: str | None) -> str:
    email = (contact_email or "").strip().lower()
    if "@" not in email:
        return ""
    local_part = email.split("@", 1)[0].strip()
    if not local_part or not EMAIL_LOCAL_PART_RE.match(local_part):
        return ""
    parts = [part for part in re.sub(r"[._-]+", " ", local_part).split() if part]
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
class PhoneWorkflowSourceContext:
    owner_id: str
    source: str
    subject: str
    body: str
    sender: str
    open_url: str
    dedupe_key: str
    received_at: datetime | None
    recruiter_email_row_id: int | None
    external_opportunity_row_id: int | None
    end_client: str = ""
    implementation_partner: str = ""
    domain: str = ""
    skills_text: str = ""
    resume_file_name: str = ""

    def __post_init__(self) -> None:
        if (self.recruiter_email_row_id is None) == (self.external_opportunity_row_id is None):
            raise ValueError("Exactly one source row id must be set")


def _context_from_recruiter_email(
    email: RecruiterEmail,
    *,
    source: str = "gmail",
) -> PhoneWorkflowSourceContext:
    return PhoneWorkflowSourceContext(
        owner_id=email.owner_id,
        source=source,
        subject=email.subject or "",
        body=email.body or "",
        sender=email.sender or "",
        open_url=email.gmail_message_url or "",
        dedupe_key=email.external_message_id or f"manual-{email.id}",
        received_at=email.gmail_received_at,
        recruiter_email_row_id=email.id,
        external_opportunity_row_id=None,
        end_client=email.end_client or "",
        implementation_partner=email.implementation_partner or "",
        domain=email.domain or "",
        skills_text=email.skills_text or "",
        resume_file_name=email.resume_file_name or "",
    )


def _context_from_external_opportunity(
    item: ExternalOpportunity,
    jd_body: str,
) -> PhoneWorkflowSourceContext:
    return PhoneWorkflowSourceContext(
        owner_id=item.owner_id,
        source="nvoids",
        subject=item.role or "",
        body=jd_body or item.raw_body or "",
        sender=item.recruiter_email or "",
        open_url=item.source_url or "",
        dedupe_key=f"nvoids:{item.external_post_id}",
        received_at=item.posted_at,
        recruiter_email_row_id=None,
        external_opportunity_row_id=item.id,
        end_client=item.company or "",
        skills_text=item.skills_text or "",
    )


@dataclass(frozen=True)
class OpportunitySnapshot:
    recruiter_number_id: int
    source_email_id: int | None
    external_opportunity_id: int | None
    gmail_message_id: str
    source_type: str
    source_url: str | None
    email_subject: str
    email_sender: str
    gmail_open_url: str
    received_at: datetime | None
    job_title: str
    end_client: str
    location: str
    work_mode: str
    visa_restrictions: str
    resume_file_name: str
    implementation_partner: str
    prime_vendor: str
    domain: str
    extracted_skills: str
    evidence: str


@dataclass(frozen=True)
class _IdempotencyPoint:
    contact: PremiumNumberContact | None
    existing_review: NumberReviewQueue | None
    existing_opportunity: RecruiterOpportunity | None


def apply_contact_version(
    contact: PremiumNumberContact,
    lead: PremiumNumberLead,
    role: str,
) -> None:
    if role == "recruiter":
        contact.is_recruiter = True
        contact.active_recruiter_lead_id = lead.id
        contact.recruiter_name = lead.owner_name or "Unknown"
        contact.designation = lead.designation or "Unknown"
        contact.recruiter_email = lead.contact_email or ""
        if lead.company and lead.company.strip().lower() != "unknown":
            contact.company = lead.company
    else:
        contact.is_employer = True
        contact.active_employer_lead_id = lead.id
        contact.owner_name = lead.owner_name or "Unknown"
        if lead.company and lead.company.strip().lower() != "unknown":
            contact.company = lead.company


class PhoneIntelligenceWorkflowService:
    def __init__(self, *, manage_transaction: bool = True):
        self.manage_transaction = manage_transaction

    def process_email(
        self,
        db: Session,
        email: RecruiterEmail,
        *,
        source: str,
    ) -> PhoneIntelligenceWorkflowResult:
        return self._run(
            db,
            _context_from_recruiter_email(email, source=source),
            include_premium_lead_upsert=True,
            include_intelligence=True,
        )

    def capture_premium_numbers(
        self,
        db: Session,
        email: RecruiterEmail,
    ) -> PhoneIntelligenceWorkflowResult:
        return self.process_email(db, email, source="gmail")

    def extract_only(self, db: Session, email: RecruiterEmail, *, source: str) -> int:
        result = self._run(
            db,
            _context_from_recruiter_email(email, source=source),
            include_premium_lead_upsert=True,
            include_intelligence=False,
        )
        return result.stored_count

    def classify_only(
        self,
        db: Session,
        email: RecruiterEmail,
        *,
        source: str,
    ) -> PhoneIntelligenceWorkflowResult:
        return self._run(
            db,
            _context_from_recruiter_email(email, source=source),
            include_premium_lead_upsert=False,
            include_intelligence=True,
        )

    def capture_premium_numbers_for_nvoids(
        self,
        db: Session,
        item: ExternalOpportunity,
        jd_body: str,
    ) -> PhoneIntelligenceWorkflowResult:
        return self._run(
            db,
            _context_from_external_opportunity(item, jd_body),
            include_premium_lead_upsert=True,
            include_intelligence=True,
        )

    def _run(
        self,
        db: Session,
        context: PhoneWorkflowSourceContext,
        *,
        include_premium_lead_upsert: bool,
        include_intelligence: bool,
    ) -> PhoneIntelligenceWorkflowResult:
        try:
            leads = self._extract_leads(db, context)
            stored_count = 0
            review_created = 0
            review_existing = 0
            recruiter_matches = 0
            employer_matches = 0
            opportunity_created = 0
            opportunity_existing = 0

            for lead in leads:
                version: PremiumNumberLead | None = None
                if include_premium_lead_upsert:
                    version, stored = self._upsert_premium_lead(db, context, lead)
                    stored_count += stored

                if not include_intelligence:
                    continue

                point = self._idempotency_point(db, context, lead)
                promotion_role = self._promotion_role(lead)
                if point.existing_review:
                    review_existing += 1
                    self._refresh_review(point.existing_review, context, lead, version)

                if promotion_role:
                    contact, created = self._find_or_create_contact(
                        db,
                        context,
                        lead,
                        point.contact,
                    )
                    if not created:
                        self._snapshot_legacy_contact_if_needed(db, contact, promotion_role)
                    if version:
                        self._link_lead_to_contact(contact, version, promotion_role)
                    else:
                        self._apply_unversioned_contact_fields(contact, lead, promotion_role)

                    if promotion_role == "recruiter":
                        recruiter_matches += 1
                        if point.existing_opportunity:
                            opportunity_existing += 1
                        else:
                            self._create_opportunity(
                                db,
                                context.owner_id,
                                self._build_snapshot(contact.id, context, lead),
                            )
                            opportunity_created += 1
                    else:
                        employer_matches += 1

                    if point.existing_review:
                        point.existing_review.state = f"classified_{promotion_role}"
                    continue

                if point.existing_review:
                    continue

                db.add(
                    NumberReviewQueue(
                        owner_id=context.owner_id,
                        source_email_id=context.recruiter_email_row_id,
                        source_external_opportunity_id=context.external_opportunity_row_id,
                        source_lead_id=version.id if version else None,
                        normalized_phone_number=lead.phone_number_normalized,
                        display_phone_number=lead.phone_number_display,
                        owner_name=lead.owner_name,
                        company=lead.company,
                        designation=lead.designation,
                        confidence=lead.confidence,
                        purpose=lead.purpose,
                        evidence_snippet=lead.source_fragment,
                        email_subject=context.subject,
                        email_sender=context.sender,
                        contact_email=lead.contact_email,
                        contact_type=lead.contact_type,
                        recruiter_relevance_score=lead.recruiter_relevance_score,
                        relevance_reason=lead.relevance_reason,
                        extraction_source=lead.extraction_source,
                        scored_with="current",
                        gmail_open_url=context.open_url,
                        state="pending",
                    )
                )
                review_created += 1

            self._finalize(db)
            return PhoneIntelligenceWorkflowResult(
                source=context.source,
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

    def _extract_leads(
        self,
        db: Session,
        context: PhoneWorkflowSourceContext,
    ) -> list[ExtractedContactGroup]:
        leads = extract_phone_leads(
            context.sender,
            context.subject,
            context.body,
            employer_domains=employer_domains_for_owner(db, context.owner_id),
        )
        if context.source == "nvoids" and context.sender:
            leads = [
                replace(lead, contact_email=context.sender)
                if not lead.contact_email and index == 0
                else lead
                for index, lead in enumerate(leads)
            ]
        return leads

    def _upsert_premium_lead(
        self,
        db: Session,
        context: PhoneWorkflowSourceContext,
        lead: ExtractedContactGroup,
    ) -> tuple[PremiumNumberLead, int]:
        query = db.query(PremiumNumberLead).filter(
            PremiumNumberLead.owner_id == context.owner_id,
            PremiumNumberLead.phone_number_normalized == lead.phone_number_normalized,
            PremiumNumberLead.role == lead.role,
        )
        if context.recruiter_email_row_id is not None:
            query = query.filter(PremiumNumberLead.recruiter_email_id == context.recruiter_email_row_id)
        else:
            query = query.filter(
                PremiumNumberLead.external_opportunity_id == context.external_opportunity_row_id
            )
        row = query.first()
        if row is None:
            row = PremiumNumberLead(
                owner_id=context.owner_id,
                recruiter_email_id=context.recruiter_email_row_id,
                external_opportunity_id=context.external_opportunity_row_id,
                phone_number_normalized=lead.phone_number_normalized,
                phone_number_display=lead.phone_number_display,
            )
            db.add(row)

        row.role = lead.role
        row.extraction_source = lead.extraction_source
        row.contact_email = lead.contact_email
        row.owner_name = lead.owner_name
        row.company = lead.company
        row.designation = lead.designation
        row.purpose = lead.purpose
        row.confidence = lead.confidence
        row.contact_type = lead.contact_type
        row.recruiter_relevance_score = lead.recruiter_relevance_score
        row.is_recruiter_relevant = lead.is_recruiter_relevant
        row.relevance_reason = lead.relevance_reason
        row.source_fragment = lead.source_fragment
        row.source_email_sender = context.sender
        row.source_email_subject = context.subject
        row.source_email_message_id = context.dedupe_key
        row.source_url = context.open_url or None
        db.flush()
        return row, 1

    def _idempotency_point(
        self,
        db: Session,
        context: PhoneWorkflowSourceContext,
        lead: ExtractedContactGroup,
    ) -> _IdempotencyPoint:
        contact = (
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == context.owner_id,
                PremiumNumberContact.normalized_phone_number == lead.phone_number_normalized,
            )
            .first()
        )
        review_query = db.query(NumberReviewQueue).filter(
            NumberReviewQueue.owner_id == context.owner_id,
            NumberReviewQueue.normalized_phone_number == lead.phone_number_normalized,
        )
        if context.recruiter_email_row_id is not None:
            review_query = review_query.filter(
                NumberReviewQueue.source_email_id == context.recruiter_email_row_id
            )
        else:
            review_query = review_query.filter(
                NumberReviewQueue.source_external_opportunity_id
                == context.external_opportunity_row_id
            )
        existing_review = review_query.first()
        existing_opportunity = None
        if contact and contact.is_recruiter:
            existing_opportunity = (
                db.query(RecruiterOpportunity)
                .filter(
                    RecruiterOpportunity.owner_id == context.owner_id,
                    RecruiterOpportunity.recruiter_number_id == contact.id,
                    RecruiterOpportunity.gmail_message_id == context.dedupe_key,
                )
                .first()
            )
        return _IdempotencyPoint(contact, existing_review, existing_opportunity)

    @staticmethod
    def _promotion_role(lead: ExtractedContactGroup) -> str | None:
        if lead.role == "employer" and "employer_domain" in lead.relevance_reason:
            return "employer"
        if (
            lead.role == "recruiter"
            and lead.is_recruiter_relevant
            and lead.recruiter_relevance_score >= 70
        ):
            return "recruiter"
        return None

    @staticmethod
    def _refresh_review(
        review: NumberReviewQueue,
        context: PhoneWorkflowSourceContext,
        lead: ExtractedContactGroup,
        version: PremiumNumberLead | None,
    ) -> None:
        review.owner_name = lead.owner_name
        review.company = lead.company
        review.designation = lead.designation
        review.display_phone_number = lead.phone_number_display
        review.confidence = lead.confidence
        review.purpose = lead.purpose
        review.evidence_snippet = lead.source_fragment
        review.email_subject = context.subject
        review.email_sender = context.sender
        review.contact_email = lead.contact_email
        review.contact_type = lead.contact_type
        review.recruiter_relevance_score = lead.recruiter_relevance_score
        review.relevance_reason = lead.relevance_reason
        review.extraction_source = lead.extraction_source
        review.source_lead_id = version.id if version else review.source_lead_id
        review.scored_with = "current"
        review.gmail_open_url = context.open_url

    @staticmethod
    def _find_or_create_contact(
        db: Session,
        context: PhoneWorkflowSourceContext,
        lead: ExtractedContactGroup,
        contact: PremiumNumberContact | None,
    ) -> tuple[PremiumNumberContact, bool]:
        if contact:
            return contact, False
        contact = PremiumNumberContact(
            owner_id=context.owner_id,
            normalized_phone_number=lead.phone_number_normalized,
            display_phone_number=lead.phone_number_display,
            first_detected_email_id=context.recruiter_email_row_id,
            source_email_id=context.recruiter_email_row_id,
        )
        db.add(contact)
        db.flush()
        return contact, True

    @staticmethod
    def _apply_unversioned_contact_fields(
        contact: PremiumNumberContact,
        lead: ExtractedContactGroup,
        role: str,
    ) -> None:
        if role == "recruiter":
            contact.is_recruiter = True
            contact.recruiter_name = lead.owner_name or "Unknown"
            contact.designation = lead.designation or "Unknown"
            contact.recruiter_email = lead.contact_email or ""
        else:
            contact.is_employer = True
            contact.owner_name = lead.owner_name or "Unknown"
        if lead.company and lead.company.strip().lower() != "unknown":
            contact.company = lead.company

    def _snapshot_legacy_contact_if_needed(
        self,
        db: Session,
        contact: PremiumNumberContact,
        role: str,
    ) -> None:
        if role == "recruiter" and not contact.is_recruiter:
            return
        if role == "employer" and not contact.is_employer:
            return
        pointer = (
            contact.active_recruiter_lead_id
            if role == "recruiter"
            else contact.active_employer_lead_id
        )
        if pointer is not None:
            return
        existing = (
            db.query(PremiumNumberLead.id)
            .filter(
                PremiumNumberLead.owner_id == contact.owner_id,
                PremiumNumberLead.contact_id == contact.id,
                PremiumNumberLead.role == role,
            )
            .first()
        )
        if existing:
            return
        snapshot = PremiumNumberLead(
            owner_id=contact.owner_id,
            recruiter_email_id=(
                contact.first_detected_email_id if role == "recruiter" else contact.source_email_id
            ),
            contact_id=contact.id,
            phone_number_normalized=contact.normalized_phone_number,
            phone_number_display=contact.display_phone_number,
            role=role,
            extraction_source="legacy_snapshot",
            contact_email=contact.recruiter_email if role == "recruiter" else "",
            owner_name=(contact.recruiter_name if role == "recruiter" else contact.owner_name),
            company=contact.company,
            designation=contact.designation if role == "recruiter" else "Unknown",
            purpose="Legacy contact snapshot",
            confidence="low",
            contact_type="legacy",
            recruiter_relevance_score=0,
            is_recruiter_relevant=role == "recruiter",
            relevance_reason="legacy_snapshot",
            source_fragment="",
            source_email_sender="",
            source_email_subject="",
        )
        db.add(snapshot)
        db.flush()
        apply_contact_version(contact, snapshot, role)

    @staticmethod
    def _link_lead_to_contact(
        contact: PremiumNumberContact,
        lead: PremiumNumberLead,
        role: str,
    ) -> None:
        lead.contact_id = contact.id
        pointer = (
            contact.active_recruiter_lead_id
            if role == "recruiter"
            else contact.active_employer_lead_id
        )
        if pointer is None:
            apply_contact_version(contact, lead, role)
        elif role == "recruiter":
            contact.is_recruiter = True
        else:
            contact.is_employer = True

    @staticmethod
    def _extract_job_metadata(subject: str, body: str) -> tuple[str, str, str, str]:
        content = f"{subject}\n{body}"
        content_l = content.lower()
        work_mode = (
            "Remote"
            if "remote" in content_l
            else ("Hybrid" if "hybrid" in content_l else ("Onsite" if "onsite" in content_l else ""))
        )
        visa_restrictions = (
            "Mentioned"
            if any(token in content_l for token in ("visa", "c2c", "w2", "1099"))
            else ""
        )
        role_match = re.search(
            r"(?:role|position|title)\s*[:\-]\s*([^\n,;]+)",
            content,
            flags=re.IGNORECASE,
        )
        location_match = re.search(
            r"(?:location)\s*[:\-]\s*([^\n,;]+)",
            content,
            flags=re.IGNORECASE,
        )
        return (
            role_match.group(1).strip() if role_match else subject.strip(),
            location_match.group(1).strip() if location_match else "",
            work_mode,
            visa_restrictions,
        )

    def _build_snapshot(
        self,
        recruiter_number_id: int,
        context: PhoneWorkflowSourceContext,
        lead: ExtractedContactGroup,
    ) -> OpportunitySnapshot:
        job_title, location, work_mode, visa_restrictions = self._extract_job_metadata(
            context.subject,
            context.body,
        )
        return OpportunitySnapshot(
            recruiter_number_id=recruiter_number_id,
            source_email_id=context.recruiter_email_row_id,
            external_opportunity_id=context.external_opportunity_row_id,
            gmail_message_id=context.dedupe_key,
            source_type="nvoids" if context.source == "nvoids" else "gmail",
            source_url=context.open_url or None,
            email_subject=context.subject,
            email_sender=context.sender,
            gmail_open_url=context.open_url,
            received_at=context.received_at or datetime.now(UTC),
            job_title=job_title,
            end_client=context.end_client,
            location=location,
            work_mode=work_mode,
            visa_restrictions=visa_restrictions,
            resume_file_name=context.resume_file_name,
            implementation_partner=context.implementation_partner,
            prime_vendor="",
            domain=context.domain,
            extracted_skills=context.skills_text,
            evidence=lead.source_fragment or "Extracted from source context",
        )

    @staticmethod
    def _create_opportunity(
        db: Session,
        owner_id: str,
        snapshot: OpportunitySnapshot,
    ) -> RecruiterOpportunity:
        row = RecruiterOpportunity(
            owner_id=owner_id,
            recruiter_number_id=snapshot.recruiter_number_id,
            source_email_id=snapshot.source_email_id,
            gmail_message_id=snapshot.gmail_message_id,
            source_type=snapshot.source_type,
            source_url=snapshot.source_url,
            external_opportunity_id=snapshot.external_opportunity_id,
            email_subject=snapshot.email_subject,
            email_sender=snapshot.email_sender,
            gmail_open_url=snapshot.gmail_open_url,
            received_at=snapshot.received_at,
            job_title=snapshot.job_title,
            end_client=snapshot.end_client,
            location=snapshot.location,
            work_mode=snapshot.work_mode,
            visa_restrictions=snapshot.visa_restrictions,
            resume_file_name=snapshot.resume_file_name,
            implementation_partner=snapshot.implementation_partner,
            prime_vendor=snapshot.prime_vendor,
            domain=snapshot.domain,
            extracted_skills=snapshot.extracted_skills,
            evidence=snapshot.evidence,
            status="New",
            notes="",
        )
        db.add(row)
        db.flush()
        return row

    def _finalize(self, db: Session) -> None:
        if self.manage_transaction:
            db.commit()
        else:
            db.flush()

    def _rollback(self, db: Session) -> None:
        if self.manage_transaction:
            db.rollback()
