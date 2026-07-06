from __future__ import annotations

import json
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.ai.draft_formatting import normalize_draft_text_size
from app.config import settings
from app.models import UserSettings
from app.phase0 import DEFAULT_FALLBACK_DRAFT_TEMPLATE, DEFAULT_SIGNATURE_EMAIL, DEFAULT_SIGNATURE_NAME, DEFAULT_SIGNATURE_PHONE, normalize_employer_domains
from app.query_bucket import sanitize_saved_queries
from app.services import policy_service


class SettingsBootstrapService:
    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _poll_interval_minutes(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.feature_auto_poll_interval_minutes or 10), 1440))

    @staticmethod
    def _nvoids_poll_interval_minutes(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.feature_nvoids_poll_interval_minutes or 30), 1440))

    @staticmethod
    def _nvoids_batch_limit(user_settings: UserSettings) -> int:
        return max(1, min(int(user_settings.nvoids_batch_limit or 10), 50))

    @staticmethod
    def _nvoids_detail_title_mode(user_settings: UserSettings) -> str:
        normalized = str(user_settings.nvoids_detail_title_mode or "").strip().lower()
        if normalized in {"job_details", "hotlist_details", "all"}:
            return normalized
        return "job_details"

    @staticmethod
    def _read_saved_gmail_queries(raw: str | None) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return []
        return sanitize_saved_queries(parsed)

    def ensure_default_settings(self) -> None:
        db = self._session_factory()
        try:
            existing = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
            if existing:
                normalized_saved_queries_json = json.dumps(
                    self._read_saved_gmail_queries(existing.saved_gmail_queries_json), separators=(",", ":")
                )
                if existing.saved_gmail_queries_json != normalized_saved_queries_json:
                    existing.saved_gmail_queries_json = normalized_saved_queries_json
                if not existing.policy_json:
                    existing.policy_json = json.dumps(policy_service.default_policy(), separators=(",", ":"))
                normalized_draft_text_size = normalize_draft_text_size(existing.draft_text_size)
                raw_nvoids_detail_title_mode = existing.nvoids_detail_title_mode
                normalized_nvoids_detail_title_mode = self._nvoids_detail_title_mode(existing)
                if existing.draft_text_size != normalized_draft_text_size:
                    existing.draft_text_size = normalized_draft_text_size
                if not existing.fallback_draft_template:
                    existing.fallback_draft_template = DEFAULT_FALLBACK_DRAFT_TEMPLATE
                if not existing.signature_name:
                    existing.signature_name = DEFAULT_SIGNATURE_NAME
                if not existing.signature_phone:
                    existing.signature_phone = DEFAULT_SIGNATURE_PHONE
                if not existing.signature_email:
                    existing.signature_email = DEFAULT_SIGNATURE_EMAIL
                if existing.preferred_employer_cc_email is None:
                    existing.preferred_employer_cc_email = ""
                if not (existing.default_gmail_query or "").strip():
                    existing.default_gmail_query = (existing.gmail_query or "").strip() or "is:unread in:inbox recruiter"
                existing.default_date_mode = policy_service.normalize_default_date_mode(existing.default_date_mode)
                existing.feature_auto_poll_interval_minutes = self._poll_interval_minutes(existing)
                existing.feature_nvoids_poll_interval_minutes = self._nvoids_poll_interval_minutes(existing)
                existing.nvoids_batch_limit = self._nvoids_batch_limit(existing)
                existing.nvoids_detail_title_mode = normalized_nvoids_detail_title_mode
                if (
                    not existing.policy_json
                    or existing.draft_text_size != normalized_draft_text_size
                    or raw_nvoids_detail_title_mode != normalized_nvoids_detail_title_mode
                    or not existing.fallback_draft_template
                    or not existing.signature_name
                    or not existing.signature_phone
                    or not existing.signature_email
                    or existing.preferred_employer_cc_email is None
                    or not (existing.default_gmail_query or "").strip()
                    or existing.saved_gmail_queries_json != normalized_saved_queries_json
                ):
                    db.commit()
                return

            default_settings = UserSettings(
                owner_id=settings.owner_id,
                enabled=True,
                gmail_query="is:unread in:inbox recruiter",
                default_gmail_query="is:unread in:inbox recruiter",
                saved_gmail_queries_json="[]",
                mail_date=None,
                default_date_mode="today",
                min_salary=0,
                accepted_locations="",
                visa_required_allowed=True,
                remote_preference="any",
                role_keywords="java,developer,spring,microservices",
                must_have_skills="java,spring",
                employer_domains=",".join(sorted(normalize_employer_domains([]))),
                free_text_guidance="",
                qualification_threshold=settings.qualification_threshold,
                feature_auto_polling=settings.feature_auto_polling,
                feature_auto_poll_interval_minutes=max(1, int(settings.feature_auto_poll_interval_minutes or 10)),
                feature_nvoids_enabled=True,
                feature_nvoids_auto_sync=False,
                feature_nvoids_poll_interval_minutes=30,
                nvoids_batch_limit=10,
                nvoids_detail_title_mode="job_details",
                nvoids_locations="",
                feature_auto_send=settings.feature_auto_send,
                feature_retry_queue=settings.feature_retry_queue,
                feature_ai_enabled=False,
                feature_ai_extractor_enabled=False,
                feature_semantic_enabled=False,
                feature_groq_job_parser_enabled=False,
                draft_text_size="normal",
                fallback_draft_template=DEFAULT_FALLBACK_DRAFT_TEMPLATE,
                signature_name=DEFAULT_SIGNATURE_NAME,
                signature_phone=DEFAULT_SIGNATURE_PHONE,
                signature_email=DEFAULT_SIGNATURE_EMAIL,
                preferred_employer_cc_email="",
                policy_json=json.dumps(policy_service.default_policy()),
            )
            db.add(default_settings)
            db.commit()
        finally:
            db.close()

    def get_settings(self, db: Session) -> UserSettings:
        user_settings = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
        if not user_settings:
            raise HTTPException(status_code=500, detail="Settings not initialized")
        return user_settings
