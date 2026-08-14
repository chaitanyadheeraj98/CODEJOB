from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.models import (
    EmailConversation,
    EmployerNumber,
    NumberReviewQueue,
    PremiumNumberLead,
    RecentRun,
    RecentRunSkippedItem,
    RecruiterEmail,
    RecruiterNumber,
    RecruiterOpportunity,
)
from app.premium_numbers.phone_normalization import canonicalize_phone


MAX_EMAIL_SEARCH_HITS = 200

SECTION_NEEDS_REVIEW = "needs_review"
SECTION_FAILED_MAPPING = "failed_mapping"
SECTION_SENT_ITEMS = "sent_items"
SECTION_PREMIUM_NUMBERS = "premium_numbers"
SECTION_INBOX = "inbox"
SECTION_RECENT_RUNS = "recent_runs"
SECTION_OTHER = "other"

_STATE_TO_SECTION = {
    "needs_review": SECTION_NEEDS_REVIEW,
    "failed": SECTION_FAILED_MAPPING,
    "approved_sent": SECTION_SENT_ITEMS,
}

_DEFAULT_SECTION_ORDER = [
    SECTION_PREMIUM_NUMBERS,
    SECTION_NEEDS_REVIEW,
    SECTION_FAILED_MAPPING,
    SECTION_SENT_ITEMS,
    SECTION_INBOX,
    SECTION_RECENT_RUNS,
    SECTION_OTHER,
]


def _section_priority_map(current_section: str | None) -> dict[str, int]:
    order = list(_DEFAULT_SECTION_ORDER)
    if current_section in order:
        order.remove(current_section)
        order.insert(0, current_section)
    return {section: index for index, section in enumerate(order)}


@dataclass(frozen=True)
class EmailSearchHit:
    section: str
    recruiter_email_id: int | None
    sender: str
    subject: str
    state: str
    detail: dict[str, object]
    occurred_at: datetime


def _query_limit() -> int:
    # Keep one extra result so the HTTP layer can report that the response was truncated.
    return MAX_EMAIL_SEARCH_HITS + 1


def _is_hidden_nvoids_placeholder_recruiter(row: RecruiterNumber) -> bool:
    normalized = str(row.normalized_phone_number or "").strip().lower()
    display = str(row.display_phone_number or "").strip().lower()
    return normalized.startswith("nvoids-") and display == "unknown" and row.first_detected_email_id is None


def _is_hidden_invalid_employer_number(row: EmployerNumber) -> bool:
    display = str(row.display_phone_number or "").strip()
    if not display or display.lower() == "unknown":
        return False
    normalized = str(row.normalized_phone_number or "").strip()
    return not canonicalize_phone(normalized) and not canonicalize_phone(display)


def search_email(
    db: Session, *, owner_id: str, query: str, current_section: str | None = None
) -> list[EmailSearchHit]:
    normalized = query.strip()
    if not normalized:
        return []

    like = f"%{normalized}%"
    try:
        parsed_id = int(normalized)
    except ValueError:
        parsed_id = None

    digits = re.sub(r"\D", "", normalized)
    phone_like = f"%{digits}%" if digits else None

    anchor_conditions = [
        RecruiterEmail.sender.ilike(like),
        RecruiterEmail.recipient_email.ilike(like),
        RecruiterEmail.cc_email.ilike(like),
        RecruiterEmail.subject.ilike(like),
        RecruiterEmail.role.ilike(like),
        RecruiterEmail.location.ilike(like),
        RecruiterEmail.skills_text.ilike(like),
    ]
    if parsed_id is not None:
        anchor_conditions.append(RecruiterEmail.id == parsed_id)

    anchors = (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == owner_id,
            or_(*anchor_conditions),
        )
        .order_by(RecruiterEmail.updated_at.desc(), RecruiterEmail.id.desc())
        .limit(_query_limit())
        .all()
    )
    anchor_by_id = {row.id: row for row in anchors}
    anchor_ids = list(anchor_by_id)

    hits = [
        EmailSearchHit(
            section=_STATE_TO_SECTION.get(row.state, SECTION_OTHER),
            recruiter_email_id=row.id,
            sender=row.sender,
            subject=row.subject,
            state=row.state,
            detail={
                "recipient_email": row.recipient_email or "",
                "cc_email": row.cc_email or "",
            },
            occurred_at=row.sent_at or row.updated_at or row.created_at,
        )
        for row in anchors
    ]

    premium_conditions = [
        PremiumNumberLead.source_email_sender.ilike(like),
        PremiumNumberLead.source_email_subject.ilike(like),
        PremiumNumberLead.phone_number_display.ilike(like),
        PremiumNumberLead.owner_name.ilike(like),
        PremiumNumberLead.company.ilike(like),
        PremiumNumberLead.designation.ilike(like),
        PremiumNumberLead.purpose.ilike(like),
    ]
    if phone_like:
        premium_conditions.append(PremiumNumberLead.phone_number_normalized.ilike(phone_like))
    if anchor_ids:
        premium_conditions.append(PremiumNumberLead.recruiter_email_id.in_(anchor_ids))
    premium_rows = (
        db.query(PremiumNumberLead, RecruiterEmail)
        .join(RecruiterEmail, RecruiterEmail.id == PremiumNumberLead.recruiter_email_id)
        .filter(
            PremiumNumberLead.owner_id == owner_id,
            RecruiterEmail.owner_id == owner_id,
            or_(*premium_conditions),
        )
        .order_by(PremiumNumberLead.updated_at.desc(), PremiumNumberLead.id.desc())
        .limit(_query_limit())
        .all()
    )
    for lead, root_email in premium_rows:
        hits.append(
            EmailSearchHit(
                section=SECTION_PREMIUM_NUMBERS,
                recruiter_email_id=root_email.id,
                sender=lead.source_email_sender or root_email.sender,
                subject=lead.source_email_subject or root_email.subject,
                state=root_email.state,
                detail={
                    "premium_number_lead_id": lead.id,
                    "phone_number_display": lead.phone_number_display,
                    "company": lead.company,
                    "confidence": lead.confidence,
                },
                occurred_at=lead.updated_at or lead.created_at,
            )
        )

    review_conditions = [
        NumberReviewQueue.display_phone_number.ilike(like),
        NumberReviewQueue.owner_name.ilike(like),
        NumberReviewQueue.company.ilike(like),
        NumberReviewQueue.designation.ilike(like),
        NumberReviewQueue.email_sender.ilike(like),
        NumberReviewQueue.email_subject.ilike(like),
    ]
    if phone_like:
        review_conditions.append(NumberReviewQueue.normalized_phone_number.ilike(phone_like))
    review_rows = (
        db.query(NumberReviewQueue)
        .filter(
            NumberReviewQueue.owner_id == owner_id,
            NumberReviewQueue.state == "pending",
            or_(*review_conditions),
        )
        .order_by(NumberReviewQueue.updated_at.desc(), NumberReviewQueue.id.desc())
        .limit(_query_limit())
        .all()
    )
    for review in review_rows:
        hits.append(
            EmailSearchHit(
                section=SECTION_PREMIUM_NUMBERS,
                recruiter_email_id=review.source_email_id,
                sender=review.email_sender,
                subject=review.email_subject,
                state="pending_review",
                detail={
                    "number_review_id": review.id,
                    "phone_number_display": review.display_phone_number,
                    "company": review.company,
                    "confidence": review.confidence,
                },
                occurred_at=review.updated_at or review.created_at,
            )
        )

    recruiter_number_conditions = [
        RecruiterNumber.display_phone_number.ilike(like),
        RecruiterNumber.recruiter_name.ilike(like),
        RecruiterNumber.company.ilike(like),
        RecruiterNumber.designation.ilike(like),
        RecruiterNumber.recruiter_email.ilike(like),
    ]
    if phone_like:
        recruiter_number_conditions.append(RecruiterNumber.normalized_phone_number.ilike(phone_like))
    recruiter_number_rows = (
        db.query(RecruiterNumber)
        .filter(
            RecruiterNumber.owner_id == owner_id,
            or_(*recruiter_number_conditions),
        )
        .order_by(RecruiterNumber.updated_at.desc(), RecruiterNumber.id.desc())
        .limit(_query_limit())
        .all()
    )
    for recruiter_number in recruiter_number_rows:
        if _is_hidden_nvoids_placeholder_recruiter(recruiter_number):
            continue
        hits.append(
            EmailSearchHit(
                section=SECTION_PREMIUM_NUMBERS,
                recruiter_email_id=recruiter_number.first_detected_email_id,
                sender=recruiter_number.recruiter_name,
                subject=recruiter_number.display_phone_number,
                state="recruiter_number",
                detail={
                    "recruiter_number_id": recruiter_number.id,
                    "phone_number_display": recruiter_number.display_phone_number,
                    "company": recruiter_number.company,
                },
                occurred_at=recruiter_number.updated_at or recruiter_number.created_at,
            )
        )

    employer_number_conditions = [
        EmployerNumber.display_phone_number.ilike(like),
        EmployerNumber.owner_name.ilike(like),
        EmployerNumber.company.ilike(like),
    ]
    if phone_like:
        employer_number_conditions.append(EmployerNumber.normalized_phone_number.ilike(phone_like))
    employer_number_rows = (
        db.query(EmployerNumber)
        .filter(
            EmployerNumber.owner_id == owner_id,
            or_(*employer_number_conditions),
        )
        .order_by(EmployerNumber.updated_at.desc(), EmployerNumber.id.desc())
        .limit(_query_limit())
        .all()
    )
    for employer_number in employer_number_rows:
        if _is_hidden_invalid_employer_number(employer_number):
            continue
        hits.append(
            EmailSearchHit(
                section=SECTION_PREMIUM_NUMBERS,
                recruiter_email_id=employer_number.source_email_id,
                sender=employer_number.owner_name,
                subject=employer_number.display_phone_number,
                state="employer_number",
                detail={
                    "employer_number_id": employer_number.id,
                    "phone_number_display": employer_number.display_phone_number,
                    "company": employer_number.company,
                },
                occurred_at=employer_number.updated_at or employer_number.created_at,
            )
        )

    opportunity_conditions = [
        RecruiterOpportunity.email_subject.ilike(like),
        RecruiterOpportunity.email_sender.ilike(like),
        RecruiterOpportunity.job_title.ilike(like),
        RecruiterOpportunity.client.ilike(like),
        RecruiterOpportunity.location.ilike(like),
        RecruiterOpportunity.extracted_skills.ilike(like),
    ]
    opportunity_rows = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == owner_id,
            or_(*opportunity_conditions),
        )
        .order_by(RecruiterOpportunity.updated_at.desc(), RecruiterOpportunity.id.desc())
        .limit(_query_limit())
        .all()
    )
    for opportunity in opportunity_rows:
        hits.append(
            EmailSearchHit(
                section=SECTION_PREMIUM_NUMBERS,
                recruiter_email_id=opportunity.source_email_id,
                sender=opportunity.email_sender,
                subject=opportunity.email_subject or opportunity.job_title,
                state=opportunity.status,
                detail={
                    "recruiter_opportunity_id": opportunity.id,
                    "job_title": opportunity.job_title,
                    "client": opportunity.client,
                },
                occurred_at=opportunity.updated_at or opportunity.created_at,
            )
        )

    if anchor_ids:
        conversation_rows = (
            db.query(EmailConversation)
            .filter(
                EmailConversation.owner_id == owner_id,
                EmailConversation.root_recruiter_email_id.in_(anchor_ids),
            )
            .order_by(EmailConversation.last_message_at.desc(), EmailConversation.id.desc())
            .limit(_query_limit())
            .all()
        )
        for conversation in conversation_rows:
            root_email = anchor_by_id[conversation.root_recruiter_email_id]
            hits.append(
                EmailSearchHit(
                    section=SECTION_INBOX,
                    recruiter_email_id=root_email.id,
                    sender=root_email.sender,
                    subject=root_email.subject,
                    state=root_email.state,
                    detail={
                        "conversation_id": conversation.id,
                        "status": conversation.status,
                        "unread_reply_count": conversation.unread_reply_count,
                        "last_message_at": conversation.last_message_at,
                    },
                    occurred_at=conversation.last_message_at,
                )
            )

    skipped_conditions = [
        RecentRunSkippedItem.sender.ilike(like),
        RecentRunSkippedItem.title_or_subject.ilike(like),
        RecentRunSkippedItem.location.ilike(like),
    ]
    if anchor_ids:
        skipped_conditions.append(RecentRunSkippedItem.candidate_email_id.in_(anchor_ids))
    skipped_rows = (
        db.query(RecentRunSkippedItem, RecruiterEmail)
        .outerjoin(
            RecruiterEmail,
            and_(
                RecruiterEmail.id == RecentRunSkippedItem.candidate_email_id,
                RecruiterEmail.owner_id == owner_id,
            ),
        )
        .filter(
            RecentRunSkippedItem.owner_id == owner_id,
            or_(*skipped_conditions),
        )
        .order_by(RecentRunSkippedItem.created_at.desc(), RecentRunSkippedItem.id.desc())
        .limit(_query_limit())
        .all()
    )
    for skipped, root_email in skipped_rows:
        hits.append(
            EmailSearchHit(
                section=SECTION_RECENT_RUNS,
                recruiter_email_id=root_email.id if root_email is not None else None,
                sender=skipped.sender or (root_email.sender if root_email is not None else ""),
                subject=skipped.title_or_subject or (root_email.subject if root_email is not None else ""),
                state=skipped.outcome,
                detail={
                    "recent_run_skipped_item_id": skipped.id,
                    "run_key": skipped.run_key,
                    "outcome": skipped.outcome,
                    "reason_code": skipped.reason_code,
                },
                occurred_at=skipped.created_at,
            )
        )

    anchors_by_batch: dict[str, list[RecruiterEmail]] = {}
    for anchor in anchors:
        if anchor.sync_batch_id:
            anchors_by_batch.setdefault(anchor.sync_batch_id, []).append(anchor)
    if anchors_by_batch:
        recent_runs = (
            db.query(RecentRun)
            .filter(
                RecentRun.owner_id == owner_id,
                RecentRun.sync_batch_id.in_(list(anchors_by_batch)),
            )
            .order_by(RecentRun.updated_at.desc(), RecentRun.id.desc())
            .limit(_query_limit())
            .all()
        )
        for recent_run in recent_runs:
            if not recent_run.sync_batch_id:
                continue
            for anchor in anchors_by_batch.get(recent_run.sync_batch_id, []):
                hits.append(
                    EmailSearchHit(
                        section=SECTION_RECENT_RUNS,
                        recruiter_email_id=anchor.id,
                        sender=anchor.sender,
                        subject=anchor.subject,
                        state=anchor.state,
                        detail={
                            "recent_run_id": recent_run.id,
                            "run_key": recent_run.run_key,
                            "status": recent_run.status,
                            "sync_batch_id": recent_run.sync_batch_id,
                        },
                        occurred_at=recent_run.updated_at or recent_run.created_at,
                    )
                )

    priority = _section_priority_map(current_section)
    hits.sort(key=lambda hit: (priority.get(hit.section, len(priority)), -hit.occurred_at.timestamp()))
    return hits[:_query_limit()]
