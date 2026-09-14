from __future__ import annotations

from sqlalchemy import func, or_

from app import tenancy
from app.db import SessionLocal
from app.mcp_server.tools import refused, untrusted
from app.models import AppTSApplication, AppTSApplicationEvent, EmailConversation, EmailReplyMessage, UserSettings
from app.services import application_service, appts_service, opportunity_lineage_service


def _enabled(db, owner_id: str) -> bool:
    row = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
    return bool(row and row.feature_applications_enabled)


def _safe(kind: str, value: str | None) -> str | None:
    return untrusted(kind, value) if value is not None else None


def _reply_summaries(
    db,
    owner_id: str,
    rows: list[AppTSApplication],
) -> dict[int, tuple[object | None, int | None]]:
    thread_ids = {row.source_thread_id for row in rows if row.source_thread_id}
    email_ids = {row.source_recruiter_email_id for row in rows if row.source_recruiter_email_id}
    conditions = []
    if thread_ids:
        conditions.append(EmailConversation.external_thread_id.in_(thread_ids))
    if email_ids:
        conditions.append(EmailConversation.root_recruiter_email_id.in_(email_ids))
    if not conditions:
        return {}
    conversations = (
        db.query(EmailConversation)
        .filter(EmailConversation.owner_id == owner_id, or_(*conditions))
        .order_by(EmailConversation.last_message_at.desc(), EmailConversation.id.desc())
        .all()
    )
    by_thread: dict[str, EmailConversation] = {}
    by_email: dict[int, EmailConversation] = {}
    for conversation in conversations:
        by_thread.setdefault(conversation.external_thread_id, conversation)
        if conversation.root_recruiter_email_id is not None:
            by_email.setdefault(conversation.root_recruiter_email_id, conversation)
    resolved = {
        row.id: (
            by_thread.get(row.source_thread_id) if row.source_thread_id else None
        ) or by_email.get(row.source_recruiter_email_id)
        for row in rows
    }
    conversation_ids = {conversation.id for conversation in resolved.values() if conversation}
    latest_replies = dict(
        db.query(
            EmailReplyMessage.conversation_id,
            func.max(EmailReplyMessage.received_at),
        )
        .filter(
            EmailReplyMessage.owner_id == owner_id,
            EmailReplyMessage.direction == "inbound",
            EmailReplyMessage.conversation_id.in_(conversation_ids),
        )
        .group_by(EmailReplyMessage.conversation_id)
        .all()
    ) if conversation_ids else {}
    return {
        application_id: (
            latest_replies.get(conversation.id),
            conversation.unread_reply_count,
        ) if conversation else (None, None)
        for application_id, conversation in resolved.items()
    }


def _application_payload(
    db,
    owner_id: str,
    row: AppTSApplication,
    *,
    include_events: bool = False,
    reply_summary: tuple[object | None, int | None] = (None, None),
) -> dict[str, object]:
    last_reply_at, unread_count = reply_summary
    payload: dict[str, object] = {
        "id": row.id,
        "job_title": _safe("email", row.job_title_snapshot),
        "company": _safe("email", row.recruiter_company_snapshot),
        "recruiter": _safe("email", row.recruiter_name_snapshot),
        "end_client": _safe("email", row.end_client_snapshot),
        "status": row.status,
        "applied_at": row.created_at.isoformat(),
        "record_id": opportunity_lineage_service.record_id_for_application(
            db,
            owner_id=owner_id,
            application=row,
        ),
        "resume_file_name": _safe("resume", row.resume_file_name_snapshot),
        "resume_version": row.resume_version_snapshot,
        "next_action_type": _safe("email", row.next_action_type),
        "next_action_at": row.next_action_at.isoformat() if row.next_action_at else None,
        "closed_reason": _safe("email", row.closed_reason),
        "tracking_origin": row.tracking_origin,
        "last_reply_at": last_reply_at.isoformat() if last_reply_at else None,
        "unread_count": unread_count,
    }
    if include_events:
        events = db.query(AppTSApplicationEvent).filter(
            AppTSApplicationEvent.owner_id == owner_id,
            AppTSApplicationEvent.application_id == row.id,
        ).order_by(AppTSApplicationEvent.occurred_at.asc(), AppTSApplicationEvent.id.asc()).all()
        payload["events"] = [
            {
                "id": event.id,
                "event_type": event.event_type,
                "event_source": event.event_source,
                "note": _safe("email", event.note),
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in events
        ]
    return payload


def list_tracked_applications(
    status: str = "",
    query: str = "",
    company: str = "",
    recruiter: str = "",
    role: str = "",
    sort: str = "newest",
    limit: int = 15,
) -> dict[str, object]:
    """Tracked applications with linked recruiter reply times and unread counts. Read-only."""
    with SessionLocal() as db:
        owner_id = tenancy.owner_id()
        if not _enabled(db, owner_id):
            return refused(
                "application tracking is off",
                hint="Turn on Application Tracking in Settings.",
            )
        try:
            rows, total, has_next = appts_service.query_applications(
                db,
                owner_id,
                status=status or None,
                q=query or None,
                company=company or None,
                recruiter=recruiter or None,
                role=role or None,
                sort=sort,
                limit=max(1, min(int(limit), 50)),
            )
        except application_service.ApplicationValidationError as exc:
            return refused(
                str(exc),
                hint="Use newest, oldest, or next_action and a valid application status.",
            )
        reply_summaries = _reply_summaries(db, owner_id, rows)
        return {
            "applications": [
                _application_payload(
                    db,
                    owner_id,
                    row,
                    reply_summary=reply_summaries.get(row.id, (None, None)),
                )
                for row in rows
            ],
            "total": total,
            "has_more": has_next,
        }


def get_tracked_application(application_id: int, include_events: bool = False) -> dict[str, object]:
    """One tracked application: stage, resume version, reminders, and optional events."""
    with SessionLocal() as db:
        owner_id = tenancy.owner_id()
        if not _enabled(db, owner_id):
            return refused(
                "application tracking is off",
                hint="Turn on Application Tracking in Settings.",
            )
        rows, _, _ = appts_service.query_applications(
            db,
            owner_id,
            application_id=application_id,
            limit=1,
        )
        if not rows:
            return refused(
                "application not found",
                hint="Choose an id returned by list_tracked_applications.",
            )
        row = rows[0]
        return _application_payload(
            db,
            owner_id,
            row,
            include_events=include_events,
            reply_summary=_reply_summaries(db, owner_id, rows).get(row.id, (None, None)),
        )
