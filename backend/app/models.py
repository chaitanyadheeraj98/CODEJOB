from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class RecruiterEmail(Base):
    __tablename__ = "recruiter_emails"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    sender: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    salary_text: Mapped[str] = mapped_column(String(255), default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    state: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_filter_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sync_batch_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    draft_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    draft_ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(50), default="pending")
    sent_status: Mapped[str] = mapped_column(String(50), default="not_sent")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_message_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gmail_received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cc_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    routing_status: Mapped[str] = mapped_column(String(50), default="unverified")
    routing_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    routing_reason: Mapped[str] = mapped_column(Text, default="")
    routing_evidence: Mapped[str] = mapped_column(Text, default="[]")
    routing_candidates: Mapped[str] = mapped_column(Text, default="[]")
    routing_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    resume_asset_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resume_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gmail_sent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    @property
    def gmail_message_url(self) -> str | None:
        if self.source != "gmail":
            return None
        if self.external_rfc_message_id:
            query = quote(f"rfc822msgid:{self.external_rfc_message_id}", safe="")
            return f"https://mail.google.com/mail/u/0/#search/{query}"
        # Prefer message id for precise targeting, fallback to thread id.
        token = self.external_message_id or self.external_thread_id
        if not token:
            return None
        return f"https://mail.google.com/mail/u/0/#all/{token}"


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    default_gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    mail_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    default_date_mode: Mapped[str] = mapped_column(String(20), default="today")
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_locations: Mapped[str] = mapped_column(Text, default="")
    visa_required_allowed: Mapped[bool] = mapped_column(default=False)
    remote_preference: Mapped[str] = mapped_column(String(50), default="any")
    role_keywords: Mapped[str] = mapped_column(Text, default="")
    must_have_skills: Mapped[str] = mapped_column(Text, default="")
    free_text_guidance: Mapped[str] = mapped_column(Text, default="")
    qualification_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    feature_auto_polling: Mapped[bool] = mapped_column(default=False)
    feature_auto_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=10)
    feature_auto_send: Mapped[bool] = mapped_column(default=False)
    feature_retry_queue: Mapped[bool] = mapped_column(default=False)
    feature_ai_enabled: Mapped[bool] = mapped_column(default=False)
    feature_semantic_enabled: Mapped[bool] = mapped_column(default=False)
    fallback_draft_template: Mapped[str] = mapped_column(Text, default="")
    signature_name: Mapped[str] = mapped_column(String(255), default="")
    signature_phone: Mapped[str] = mapped_column(String(80), default="")
    signature_email: Mapped[str] = mapped_column(String(255), default="")
    policy_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class ResumeAsset(Base):
    __tablename__ = "resume_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/pdf")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(default=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    sync_batch_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)


class DraftEditFeedback(Base):
    __tablename__ = "draft_edit_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_draft: Mapped[str] = mapped_column(Text)
    edited_draft: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class RecipientRoutingFeedback(Base):
    __tablename__ = "recipient_routing_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    sender_domain: Mapped[str] = mapped_column(String(255), index=True)
    corrected_to: Mapped[str] = mapped_column(String(255))
    corrected_cc: Mapped[str] = mapped_column(String(255))
    sample_sender: Mapped[str | None] = mapped_column(String(255), nullable=True)
    evidence_to_present: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_cc_present: Mapped[bool] = mapped_column(Boolean, default=False)
    sample_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)


class ProductivityEvent(Base):
    __tablename__ = "productivity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_source: Mapped[str] = mapped_column(String(40), default="system")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
