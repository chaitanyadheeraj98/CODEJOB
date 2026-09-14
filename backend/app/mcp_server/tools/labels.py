from __future__ import annotations

from fastapi import HTTPException

from app import tenancy
from app.db import SessionLocal
from app.mcp_server.tools import refused, untrusted
from app.models import UserSettings
from app.phase0 import DEFAULT_SIGNATURE_EMAIL
from app.services import gmail_label_service, label_dossier_service


def _safe(value: str | None) -> str | None:
    return untrusted("email", value) if value is not None else None


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
                    "message_count_snapshot": row.message_count_snapshot,
                    "last_synced_at": row.last_synced_at.isoformat(),
                }
                for row in rows
            ],
            "tracked_count": sum(row.is_tracked for row in rows),
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
