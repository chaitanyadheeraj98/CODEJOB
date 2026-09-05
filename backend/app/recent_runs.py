from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models import RecentRun, RecentRunSkippedItem

RUN_SOURCE_AUTOMATION = "automation_run"
RUN_SOURCE_GMAIL_SYNC = "gmail_sync"
RUN_SOURCE_NVOIDS_SYNC = "nvoids_sync"
# One pasted requirement. Its own run source so Recent Runs tells a paste apart
# from a sync - they are one item and thousands, and only one of them is
# something the user is standing there waiting for.
RUN_SOURCE_MANUAL_INTAKE = "manual_intake"
# One-off nvoids searches the assistant queued for a named company. Same run
# source and queue as a scheduled sync - it is the same work - but its own key
# prefix, so "did my search finish" can be answered about the right run rather
# than about whichever sync happened to be most recent.
NVOIDS_CLIENT_SEARCH_PREFIX = "nvoids_client_search:"


def automation_run_key(raw_key: str) -> str:
    return f"automation_run:{raw_key}"


def gmail_sync_run_key(sync_batch_id: str) -> str:
    return f"gmail_sync:{sync_batch_id}"


def nvoids_run_key(run_id: int) -> str:
    return f"nvoids_sync:{run_id}"


def manual_intake_run_key(paste_id: str) -> str:
    return f"manual_intake:{paste_id}"


def build_gmail_message_url(
    *,
    external_message_id: str | None = None,
    external_thread_id: str | None = None,
    external_rfc_message_id: str | None = None,
) -> str | None:
    if external_rfc_message_id:
        query = quote(f"rfc822msgid:{external_rfc_message_id}", safe="")
        return f"https://mail.google.com/mail/u/0/#search/{query}"
    token = (external_message_id or "").strip() or (external_thread_id or "").strip()
    if not token:
        return None
    return f"https://mail.google.com/mail/u/0/#all/{token}"


@dataclass(frozen=True)
class SkippedItemRecord:
    owner_id: str
    run_source: str
    run_key: str
    source_type: str
    reason_code: str
    reason_detail: str
    external_message_id: str | None = None
    external_thread_id: str | None = None
    candidate_email_id: int | None = None
    external_opportunity_id: int | None = None
    title_or_subject: str = ""
    sender: str = ""
    location: str | None = None
    source_url: str | None = None
    gmail_message_url: str | None = None
    intent_type: str | None = None
    intent_confidence: float | None = None
    intent_reason: str | None = None
    intent_evidence: list[str] | None = None
    intent_negative_evidence: list[str] | None = None
    gate_action: str | None = None
    gate_provider: str | None = None
    gate_error: str | None = None
    source_group_name: str | None = None
    source_group_email: str | None = None
    source_group_match_method: str | None = None
    source_group_trusted: bool | None = None
    qualification_result: str | None = None
    blocking_rule: str | None = None
    qualification_detail: str | None = None
    qualification_context: dict[str, Any] | None = None


def create_recent_run(
    db: Session,
    *,
    owner_id: str,
    run_source: str,
    run_key: str,
    status: str,
    detail: str,
    matched_count: int | None = None,
    queued_count: int | None = None,
    skipped_count: int = 0,
    failed_count: int = 0,
    skipped_item_count: int = 0,
    sync_batch_id: str | None = None,
    external_scrape_run_id: int | None = None,
    job_backend_id: str | None = None,
    total_items: int | None = None,
    processed_items: int = 0,
    progress_pct: float | None = None,
    queue_name: str | None = None,
) -> RecentRun:
    row = RecentRun(
        owner_id=owner_id,
        run_source=run_source,
        run_key=run_key,
        sync_batch_id=sync_batch_id,
        external_scrape_run_id=external_scrape_run_id,
        status=status,
        detail=detail,
        matched_count=matched_count,
        queued_count=queued_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        skipped_item_count=skipped_item_count,
        job_backend_id=job_backend_id,
        total_items=total_items,
        processed_items=processed_items,
        progress_pct=progress_pct,
        queue_name=queue_name,
    )
    db.add(row)
    db.flush()
    return row


def update_recent_run(
    row: RecentRun,
    *,
    status: str,
    detail: str,
    matched_count: int | None = None,
    queued_count: int | None = None,
    skipped_count: int = 0,
    failed_count: int = 0,
    skipped_item_count: int = 0,
) -> RecentRun:
    row.status = status
    row.detail = detail
    row.matched_count = matched_count
    row.queued_count = queued_count
    row.skipped_count = skipped_count
    row.failed_count = failed_count
    row.skipped_item_count = skipped_item_count
    return row


def record_skipped_item(db: Session, payload: SkippedItemRecord) -> RecentRunSkippedItem:
    gmail_message_url = payload.gmail_message_url
    if payload.source_type == "gmail" and not gmail_message_url:
        gmail_message_url = build_gmail_message_url(
            external_message_id=payload.external_message_id,
            external_thread_id=payload.external_thread_id,
        )
    row = RecentRunSkippedItem(
        owner_id=payload.owner_id,
        run_source=payload.run_source,
        run_key=payload.run_key,
        source_type=payload.source_type,
        outcome="skipped",
        reason_code=payload.reason_code,
        reason_detail=payload.reason_detail,
        external_message_id=payload.external_message_id,
        external_thread_id=payload.external_thread_id,
        candidate_email_id=payload.candidate_email_id,
        external_opportunity_id=payload.external_opportunity_id,
        title_or_subject=payload.title_or_subject,
        sender=payload.sender,
        location=payload.location,
        source_url=payload.source_url,
        gmail_message_url=gmail_message_url,
        intent_type=payload.intent_type,
        intent_confidence=payload.intent_confidence,
        intent_reason=payload.intent_reason,
        intent_evidence_json=json.dumps(payload.intent_evidence or [], separators=(",", ":")),
        intent_negative_evidence_json=json.dumps(payload.intent_negative_evidence or [], separators=(",", ":")),
        gate_action=payload.gate_action,
        gate_provider=payload.gate_provider,
        gate_error=payload.gate_error,
        source_group_name=payload.source_group_name,
        source_group_email=payload.source_group_email,
        source_group_match_method=payload.source_group_match_method,
        source_group_trusted=payload.source_group_trusted,
        qualification_result=payload.qualification_result,
        blocking_rule=payload.blocking_rule,
        qualification_detail=payload.qualification_detail,
        qualification_context_json=json.dumps(payload.qualification_context or {}, separators=(",", ":")),
    )
    db.add(row)
    db.flush()
    return row


def row_to_recent_run_dict(row: RecentRun) -> dict[str, Any]:
    return {
        "run_key": row.run_key,
        "run_source": row.run_source,
        "status": row.status,
        "detail": row.detail,
        "matched_count": row.matched_count,
        "queued_count": row.queued_count,
        "skipped_count": row.skipped_count,
        "failed_count": row.failed_count,
        "skipped_item_count": row.skipped_item_count,
        "source_count": row.source_count,
        "requirement_count": row.requirement_count,
        "multi_role_source_count": row.multi_role_source_count,
        "manifest_review_count": row.manifest_review_count,
        "job_backend_id": row.job_backend_id,
        "total_items": row.total_items,
        "processed_items": row.processed_items,
        "progress_pct": row.progress_pct,
        "queue_name": row.queue_name,
        "created_at": row.created_at,
        "sync_batch_id": row.sync_batch_id,
        "external_scrape_run_id": row.external_scrape_run_id,
    }
