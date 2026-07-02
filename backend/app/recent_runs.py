from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.models import RecentRun, RecentRunSkippedItem

RUN_SOURCE_AUTOMATION = "automation_run"
RUN_SOURCE_GMAIL_SYNC = "gmail_sync"
RUN_SOURCE_NVOIDS_SYNC = "nvoids_sync"


def automation_run_key(raw_key: str) -> str:
    return f"automation_run:{raw_key}"


def gmail_sync_run_key(sync_batch_id: str) -> str:
    return f"gmail_sync:{sync_batch_id}"


def nvoids_run_key(run_id: int) -> str:
    return f"nvoids_sync:{run_id}"


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
        "created_at": row.created_at,
        "sync_batch_id": row.sync_batch_id,
        "external_scrape_run_id": row.external_scrape_run_id,
    }
