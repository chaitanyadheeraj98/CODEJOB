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
from app.parsing.jd_requirements import extract_work_authorizations
from app.phase0 import email_domain
from app.premium_numbers.domain_guard import employer_domains_for_owner, is_derivable_company_domain
from app.premium_numbers.extraction import ExtractedContactGroup, extract_phone_leads
from app.services import opportunity_lineage_service


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


def company_fallback_for_unknown(db: Session, owner_id: str, contact_email: str | None) -> str:
    if not is_derivable_company_domain(db, owner_id, contact_email):
        return ""
    return derive_company_from_email_domain(contact_email)


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
class JobMetadataAiExtraction:
    """AI-first job metadata for a posting or email, produced by parse_email_with_details().

    Fields left blank mean the AI extractor did not run, failed, or returned no value for that
    field; callers should fall back to regex extraction (or a prior stored value) in that case.
    """

    job_title: str = ""
    location: str = ""
    work_mode: str = ""
    visa_restrictions: str = ""
    domain: str = ""
    end_client: str = ""
    implementation_partner: str = ""


def job_metadata_ai_extraction_from_parsed(parsed: dict, parser_details: dict) -> JobMetadataAiExtraction:
    job_title = str(parsed.get("role") or "").strip()
    location = str(parsed.get("location") or "").strip()
    domain = str(parsed.get("domain") or "").strip()
    end_client = str(parsed.get("end_client") or "").strip()
    implementation_partner = str(parsed.get("implementation_partner") or "").strip()

    work_mode = ""
    visa_restrictions = ""
    if parser_details.get("parser_mode") == "ai_primary":
        ai_result = parser_details.get("ai_extractor_result") or {}
        work_mode = str(ai_result.get("work_mode") or "").strip()
        visa_hints = ai_result.get("visa_hints") or []
        visa_restrictions = ", ".join(hint for hint in visa_hints if hint)

    return JobMetadataAiExtraction(
        job_title=job_title,
        location=location,
        work_mode=work_mode,
        visa_restrictions=visa_restrictions,
        domain=domain,
        end_client=end_client,
        implementation_partner=implementation_partner,
    )


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
    location: str = ""
    job_title: str = ""
    work_mode: str = ""
    visa_restrictions: str = ""

    def __post_init__(self) -> None:
        if (self.recruiter_email_row_id is None) == (self.external_opportunity_row_id is None):
            raise ValueError("Exactly one source row id must be set")


def _context_from_recruiter_email(
    email: RecruiterEmail,
    *,
    source: str = "gmail",
    ai_extraction: JobMetadataAiExtraction | None = None,
) -> PhoneWorkflowSourceContext:
    ai = ai_extraction or JobMetadataAiExtraction()
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
        end_client=ai.end_client or email.end_client or "",
        implementation_partner=ai.implementation_partner or email.implementation_partner or "",
        domain=ai.domain or email.domain or "",
        skills_text=email.skills_text or "",
        resume_file_name=email.resume_file_name or "",
        location=ai.location or email.location or "",
        # email.role is itself AI-first (parse_email_with_details already ran during ingest) -
        # read it by default so a fresh capture doesn't fall back to _extract_job_metadata's
        # crude regex (which used to leave job_title as the raw, unparsed email subject).
        job_title=ai.job_title or email.role or "",
        work_mode=ai.work_mode,
        visa_restrictions=ai.visa_restrictions,
    )


def _context_from_external_opportunity(
    item: ExternalOpportunity,
    jd_body: str,
    ai_extraction: JobMetadataAiExtraction | None = None,
) -> PhoneWorkflowSourceContext:
    ai = ai_extraction or JobMetadataAiExtraction()
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
        end_client=ai.end_client or item.company or "",
        implementation_partner=ai.implementation_partner,
        domain=ai.domain,
        skills_text=item.skills_text or "",
        location=ai.location or item.location or "",
        job_title=ai.job_title,
        work_mode=ai.work_mode,
        visa_restrictions=ai.visa_restrictions,
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
    db: Session,
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
            fallback = company_fallback_for_unknown(db, contact.owner_id, lead.contact_email)
            if fallback:
                contact.company = fallback
    else:
        contact.is_employer = True
        contact.active_employer_lead_id = lead.id
        contact.owner_name = lead.owner_name or "Unknown"
        contact.employer_email = lead.contact_email or ""
        if lead.company and lead.company.strip().lower() != "unknown":
            contact.company = lead.company
        else:
            fallback = company_fallback_for_unknown(db, contact.owner_id, lead.contact_email)
            if fallback:
                contact.company = fallback
    if lead.linkedin_url:
        contact.linkedin_url = lead.linkedin_url
    if lead.external_opportunity_id:
        contact.source_type = "nvoids"
        contact.source_id = lead.external_opportunity_id
    elif lead.recruiter_email_id:
        contact.source_type = "gmail"
        contact.source_id = lead.recruiter_email_id
    contact.source_link_url = lead.source_url


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
        ai_extraction: JobMetadataAiExtraction | None = None,
    ) -> PhoneIntelligenceWorkflowResult:
        return self._run(
            db,
            _context_from_external_opportunity(item, jd_body, ai_extraction),
            include_premium_lead_upsert=True,
            include_intelligence=True,
        )

    def refresh_nvoids_opportunity_metadata(
        self,
        db: Session,
        opportunity: RecruiterOpportunity,
        item: ExternalOpportunity,
        jd_body: str,
        ai_extraction: JobMetadataAiExtraction | None = None,
    ) -> RecruiterOpportunity:
        """Re-derive job metadata for an *already-created* Nvoids opportunity - see
        `_refresh_opportunity_metadata` for the shared contract with the Gmail equivalent.
        """
        return self._refresh_opportunity_metadata(
            db, opportunity, _context_from_external_opportunity(item, jd_body, ai_extraction)
        )

    def refresh_gmail_opportunity_metadata(
        self,
        db: Session,
        opportunity: RecruiterOpportunity,
        email: RecruiterEmail,
        ai_extraction: JobMetadataAiExtraction | None = None,
    ) -> RecruiterOpportunity:
        """Re-derive job metadata for an *already-created* Gmail opportunity - see
        `_refresh_opportunity_metadata` for the shared contract with the Nvoids equivalent.
        """
        return self._refresh_opportunity_metadata(
            db, opportunity, _context_from_recruiter_email(email, ai_extraction=ai_extraction)
        )

    def _refresh_opportunity_metadata(
        self,
        db: Session,
        opportunity: RecruiterOpportunity,
        context: PhoneWorkflowSourceContext,
    ) -> RecruiterOpportunity:
        """Re-derive job_title/location/work_mode/visa/domain/end_client/implementation_partner
        for an *already-created* opportunity (AI-first, regex fallback - same contract as a fresh
        capture) and apply them onto the existing row. `capture_premium_numbers*` only writes
        these fields once, at first creation (see `_run`'s idempotency check), so an already-
        bridged card never gets new AI-derived values without going through this path. A freshly
        computed blank never overwrites an existing non-blank value, so a manual edit or an
        earlier good extraction is never clobbered by a weaker later one.
        """
        job_title, location, work_mode, visa_restrictions = self._extract_job_metadata(
            context.subject,
            context.body,
        )
        opportunity.job_title = context.job_title or job_title or opportunity.job_title
        opportunity.location = context.location or location or opportunity.location
        opportunity.work_mode = context.work_mode or work_mode or opportunity.work_mode
        opportunity.visa_restrictions = context.visa_restrictions or visa_restrictions or opportunity.visa_restrictions
        opportunity.domain = context.domain or opportunity.domain
        opportunity.end_client = context.end_client or opportunity.end_client
        opportunity.implementation_partner = context.implementation_partner or opportunity.implementation_partner
        if self.manage_transaction:
            db.commit()
        else:
            db.flush()
        return opportunity

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
                promotion_role = self._promotion_role(lead, point.contact)
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
                        self._link_lead_to_contact(db, contact, version, promotion_role)
                    else:
                        self._apply_unversioned_contact_fields(db, contact, lead, promotion_role)

                    if promotion_role == "recruiter":
                        recruiter_matches += 1
                        if point.existing_opportunity:
                            opportunity_existing += 1
                        else:
                            opportunity = self._create_opportunity(
                                db,
                                context.owner_id,
                                self._build_snapshot(contact.id, context, lead),
                            )
                            source_type = "nvoids" if context.source == "nvoids" else "gmail"
                            source_row_id = (
                                context.external_opportunity_row_id
                                if source_type == "nvoids"
                                else context.recruiter_email_row_id
                            )
                            record_id = opportunity_lineage_service.resolve_record_id(
                                db,
                                owner_id=context.owner_id,
                                source_type=source_type,
                                external_id=str(source_row_id) if source_row_id is not None else "",
                            )
                            opportunity.record_id = record_id
                            if point.existing_review and point.existing_review.lineage_id:
                                opportunity_lineage_service.attach_recruiter_opportunity(
                                    db,
                                    lineage_id=point.existing_review.lineage_id,
                                    recruiter_opportunity_id=opportunity.id,
                                    process_name="phone_intelligence_workflow",
                                )
                                opportunity_lineage_service.link_record_to_lineage(
                                    db, record_id=record_id, lineage_id=point.existing_review.lineage_id
                                )
                            else:
                                lineage = opportunity_lineage_service.create_lineage(
                                    db,
                                    owner_id=context.owner_id,
                                    origin_type=source_type,
                                    source_type=source_type,
                                    external_id=str(source_row_id) if source_row_id is not None else "",
                                    source_url=context.open_url,
                                    process_name="phone_intelligence_workflow",
                                    recruiter_opportunity_id=opportunity.id,
                                )
                                if point.existing_review:
                                    point.existing_review.lineage_id = lineage.id
                                opportunity_lineage_service.link_record_to_lineage(
                                    db, record_id=record_id, lineage_id=lineage.id
                                )
                            opportunity_created += 1
                    else:
                        employer_matches += 1

                    if point.existing_review:
                        point.existing_review.state = f"classified_{promotion_role}"
                    continue

                if point.existing_review:
                    continue

                source_type = "nvoids" if context.source == "nvoids" else "gmail"
                source_row_id = (
                    context.external_opportunity_row_id
                    if source_type == "nvoids"
                    else context.recruiter_email_row_id
                )
                record_id = opportunity_lineage_service.resolve_record_id(
                    db,
                    owner_id=context.owner_id,
                    source_type=source_type,
                    external_id=str(source_row_id) if source_row_id is not None else "",
                )
                lineage = opportunity_lineage_service.create_lineage(
                    db,
                    owner_id=context.owner_id,
                    origin_type=source_type,
                    source_type=source_type,
                    external_id=str(source_row_id) if source_row_id is not None else "",
                    source_url=context.open_url,
                    process_name="phone_intelligence_workflow",
                )
                opportunity_lineage_service.link_record_to_lineage(
                    db, record_id=record_id, lineage_id=lineage.id
                )
                db.add(
                    NumberReviewQueue(
                        owner_id=context.owner_id,
                        lineage_id=lineage.id,
                        record_id=record_id,
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
        if lead.company and lead.company.strip().lower() != "unknown":
            row.company = lead.company
        else:
            row.company = company_fallback_for_unknown(db, context.owner_id, lead.contact_email) or lead.company
        row.linkedin_url = lead.linkedin_url
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
    def _promotion_role(
        lead: ExtractedContactGroup, existing_contact: PremiumNumberContact | None
    ) -> str | None:
        if lead.role == "employer" and "employer_domain" in lead.relevance_reason:
            return "employer"
        if (
            lead.role == "recruiter"
            and lead.is_recruiter_relevant
            and lead.recruiter_relevance_score >= 70
        ):
            if (
                existing_contact is not None
                and existing_contact.is_employer
                and not existing_contact.is_recruiter
                and existing_contact.recruiter_verification_level == "unverified"
            ):
                return None
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
            contact.deleted_at = None
            contact.source_type = context.source
            contact.source_id = context.external_opportunity_row_id or context.recruiter_email_row_id
            contact.source_link_url = context.open_url or None
            return contact, False
        contact = PremiumNumberContact(
            owner_id=context.owner_id,
            normalized_phone_number=lead.phone_number_normalized,
            display_phone_number=lead.phone_number_display,
            first_detected_email_id=context.recruiter_email_row_id,
            source_email_id=context.recruiter_email_row_id,
            source_type=context.source,
            source_id=context.external_opportunity_row_id or context.recruiter_email_row_id,
            source_link_url=context.open_url or None,
        )
        db.add(contact)
        db.flush()
        return contact, True

    @staticmethod
    def _apply_unversioned_contact_fields(
        db: Session,
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
            contact.employer_email = lead.contact_email or ""
        if lead.company and lead.company.strip().lower() != "unknown":
            contact.company = lead.company
        else:
            fallback = company_fallback_for_unknown(db, contact.owner_id, lead.contact_email)
            if fallback:
                contact.company = fallback
        if lead.linkedin_url:
            contact.linkedin_url = lead.linkedin_url

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
            contact_email=contact.recruiter_email if role == "recruiter" else contact.employer_email,
            owner_name=(contact.recruiter_name if role == "recruiter" else contact.owner_name),
            company=contact.company,
            linkedin_url=contact.linkedin_url,
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
        apply_contact_version(db, contact, snapshot, role)

    @staticmethod
    def _link_lead_to_contact(
        db: Session,
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
            apply_contact_version(db, contact, lead, role)
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
        visa_restrictions = ", ".join(extract_work_authorizations(content))
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
            job_title=context.job_title or job_title,
            end_client=context.end_client,
            location=context.location or location,
            work_mode=context.work_mode or work_mode,
            visa_restrictions=context.visa_restrictions or visa_restrictions,
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
