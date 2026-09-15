from __future__ import annotations

from fastapi import HTTPException

from app import tenancy
from app.db import SessionLocal
from app.mcp_server.tools import refused, untrusted
from app.models import UserSettings
from app.phase0 import DEFAULT_SIGNATURE_EMAIL
from app.services import gmail_label_service, label_dossier_service, label_tracking_service


def _safe(value: str | None) -> str | None:
    return untrusted("email", value) if value is not None else None


def _resolve_label(db, owner_id: str, label: str) -> str | None:
    """Match one label by id or name, case-insensitively.

    `gmail_label_service.resolve_label_ids` matches exactly, so "rtr requested"
    misses "RTR Requested" and returns no ids - which the thread query cannot
    tell apart from a label that genuinely has no threads. The tool resolves the
    name itself so it can refuse the first case instead of reporting zero.
    """
    wanted = label.strip().lower()
    for row in gmail_label_service.list_labels(db, owner_id):
        if wanted in (row.external_label_id.lower(), row.name.lower()):
            return row.external_label_id
    return None


def list_gmail_labels(tracked_only: bool = False) -> dict[str, object]:
    """Gmail labels this account knows about, and which are tracked. Read-only."""
    with SessionLocal() as db:
        rows = gmail_label_service.list_labels(
            db,
            tenancy.owner_id(),
            tracked_only=tracked_only,
            include_deleted=False,
        )
        if not rows:
            return refused(
                "no Gmail labels found",
                hint="Sync labels from the Labels workspace first.",
            )
        return {
            "labels": [
                {
                    "external_label_id": row.external_label_id,
                    "name": _safe(row.name),
                    "label_type": row.label_type,
                    "tracked": row.is_tracked,
                    # message_count_snapshot is deliberately not reported. Gmail's
                    # labels.list omits threadsTotal and gmail_client keeps only id
                    # and name, so the column is 0 for every label forever - and a
                    # model reading it answered "0 messages synced" for a label
                    # holding 8 threads. Counts come from list_label_threads.
                    "last_synced_at": row.last_synced_at.isoformat(),
                }
                for row in rows
            ],
            "tracked_count": sum(row.is_tracked for row in rows),
        }


def list_label_threads(
    label: str = "",
    query: str = "",
    status: str = "all",
    sort: str = "newest",
    page: int = 1,
    limit: int = 25,
) -> dict[str, object]:
    """Threads carrying a tracked Gmail label, newest first. Read-only.

    Call this for "what is in my Submissions label", "check the mails in RTR
    Requested", or "how many threads are under Interview". `label` takes a label
    name or an external_label_id and is matched case-insensitively; leave it
    empty for every tracked thread. `status`: all, promoted (already an
    application) or untracked. `query` matches subject or participants.

    Every row carries the `thread_id` that get_label_thread_dossier needs to read
    the messages themselves, and `total` is the true count for the filter.
    """
    with SessionLocal() as db:
        owner_id = tenancy.owner_id()
        resolved = None
        if label.strip():
            resolved = _resolve_label(db, owner_id, label)
            if resolved is None:
                return refused(
                    f"no label named {label.strip()!r}",
                    hint="Call list_gmail_labels and use a name or external_label_id it returns.",
                )
        try:
            listing = label_tracking_service.list_label_threads(
                db,
                owner_id,
                label=resolved,
                q=query or None,
                status=status,
                sort=sort,
                page=max(1, page),
                limit=max(1, min(limit, 50)),
            )
        except HTTPException as exc:
            return refused(
                str(exc.detail),
                hint="status is all, promoted or untracked; sort is newest or oldest.",
            )

        payload = listing.model_dump(mode="json")
        for thread in payload["items"]:
            for key in ("subject", "recruiter", "recruiter_email"):
                thread[key] = _safe(thread[key])
            thread["labels"] = [_safe(value) for value in thread["labels"]]
            thread.pop("gmail_thread_link", None)
        return {
            "threads": payload["items"],
            "total": payload["total"],
            "page": max(1, page),
            "has_next": payload["has_next"],
        }


def get_label_thread_dossier(thread_id: str) -> dict[str, object]:
    """Everything one labeled thread is responsible for, in one chronology."""
    with SessionLocal() as db:
        owner_id = tenancy.owner_id()
        user_settings = db.query(UserSettings).filter(UserSettings.owner_id == owner_id).first()
        owner_email = (
            ((user_settings.signature_email if user_settings else "") or "").strip()
            or DEFAULT_SIGNATURE_EMAIL
        )
        try:
            dossier = label_dossier_service.thread_dossier(
                db,
                owner_id,
                thread_id,
                owner_email=owner_email,
            ).model_dump(mode="json")
        except HTTPException as exc:
            if exc.status_code == 404:
                return refused(
                    str(exc.detail),
                    hint="Choose a thread returned by the tracked Labels workspace.",
                )
            raise

        dossier["subject"] = _safe(dossier["subject"])
        dossier["labels"] = [_safe(value) for value in dossier["labels"]]
        dossier["watches"] = [_safe(value) for value in dossier["watches"]]
        for contact in dossier["contacts"]:
            for key in ("address", "name", "domain"):
                contact[key] = _safe(contact[key])
        for message in dossier["messages"]:
            for key in ("sender", "sender_address", "to_header", "cc_header", "subject", "snippet", "body"):
                message[key] = _safe(message[key])
        return dossier
