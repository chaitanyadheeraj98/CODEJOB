from __future__ import annotations

from app.config import settings
from app.db import SessionLocal
from app.models import AttachmentAsset, GmailRequirementGroup, UserSettings
from app.runtime_state import runtime_state


def get_ai_status() -> dict[str, object]:
    """Return non-secret AI provider runtime health for this CodeJob process."""
    return {
        "chat_enabled": settings.feature_chat_enabled,
        "chat_model": runtime_state.chat_active_model or settings.ollama_chat_model,
        "chat_last_error": runtime_state.chat_last_error,
        "chat_last_success_at": (
            runtime_state.chat_last_success_at.isoformat() if runtime_state.chat_last_success_at else None
        ),
        "mcp_status": runtime_state.chat_mcp_status,
        "groq_last_error": runtime_state.groq_last_error,
        "groq_last_success_at": (
            runtime_state.groq_last_success_at.isoformat() if runtime_state.groq_last_success_at else None
        ),
    }


def get_settings_summary() -> dict[str, object]:
    """Return an owner-scoped, non-secret summary of saved automation settings."""
    db = SessionLocal()
    try:
        row = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
        if row is None:
            return {"error": "Settings not initialized"}
        attachments = (
            db.query(AttachmentAsset)
            .filter(AttachmentAsset.owner_id == settings.owner_id)
            .order_by(AttachmentAsset.created_at.desc())
            .all()
        )
        groups = (
            db.query(GmailRequirementGroup)
            .filter(GmailRequirementGroup.owner_id == settings.owner_id)
            .order_by(GmailRequirementGroup.display_name.asc())
            .all()
        )
        return {
            "enabled": row.enabled,
            "qualification_threshold": row.qualification_threshold,
            "remote_preference": row.remote_preference,
            "accepted_locations": row.accepted_locations,
            "role_keywords": row.role_keywords,
            "must_have_skills": row.must_have_skills,
            "feature_auto_polling": row.feature_auto_polling,
            "feature_auto_send": row.feature_auto_send,
            "feature_retry_queue": row.feature_retry_queue,
            "feature_groq_job_parser_enabled": row.feature_groq_job_parser_enabled,
            "feature_reply_inbox_enabled": row.feature_reply_inbox_enabled,
            "attachments": [{"file_name": a.file_name, "is_enabled": a.is_enabled} for a in attachments],
            "trusted_sender_groups": [
                {"display_name": g.display_name, "group_email": g.group_email, "enabled": g.enabled}
                for g in groups
            ],
        }
    finally:
        db.close()
