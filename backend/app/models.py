from datetime import UTC, datetime
from urllib.parse import quote

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.ai.draft_quality import assess_draft_quality
from app.db import Base, UTCDateTime


def utc_now() -> datetime:
    return datetime.now(UTC)


class RecruiterEmail(Base):
    __tablename__ = "recruiter_emails"
    __table_args__ = (
        UniqueConstraint("source_parent_email_id", "requirement_key", name="ux_recruiter_email_parent_requirement"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    sender: Mapped[str] = mapped_column(String(255), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    salary_text: Mapped[str] = mapped_column(String(255), default="")
    skills_text: Mapped[str] = mapped_column(Text, default="")
    skills_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    state: Mapped[str] = mapped_column(String(50), index=True, default="auto_rejected")
    decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    hard_filter_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    ai_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_score_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    ats_score_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ats_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    ats_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resume_picker_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_picker_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_input_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    semantic_input_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    semantic_fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    thread_snapshot_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    thread_snapshot_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_negative_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gate_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_match_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_trusted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification_result: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocking_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qualification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_batch_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    draft_reply: Mapped[str] = mapped_column(Text, default="")
    draft_source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    draft_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    draft_ai_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    draft_resume_context_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    approval_status: Mapped[str] = mapped_column(String(50), default="pending")
    sent_status: Mapped[str] = mapped_column(String(50), default="not_sent")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_message_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gmail_received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    applied_gmail_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applied_gmail_label_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    applied_gmail_label_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
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
    parser_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    end_client: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    implementation_partner: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    domain_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    interview_type: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    screening_mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_parent_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    is_source_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    is_multi_role_child: Mapped[bool] = mapped_column(Boolean, default=False)
    requirement_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requirement_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requirement_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    inherited_constraints_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_manifest_status: Mapped[str] = mapped_column(String(40), default="not_run")
    role_manifest_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    role_manifest_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_manifest_diagnostics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    eligibility_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    eligibility_details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sendability_status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    gmail_sent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tracking_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    open_count: Mapped[int] = mapped_column(Integer, default=0)
    sent_attachment_file_names_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)

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

    @property
    def gmail_sent_message_url(self) -> str | None:
        token = (self.gmail_sent_id or "").strip()
        if not token:
            return None
        return f"https://mail.google.com/mail/u/0/#all/{quote(token, safe='')}"

    @property
    def draft_quality(self) -> dict[str, object]:
        return assess_draft_quality(
            draft_text=self.draft_reply or "",
            ai_score=self.ai_score,
            routing_confidence=self.routing_confidence,
            resume_context_status=self.draft_resume_context_status,
            recipient_email=self.recipient_email,
            cc_email=self.cc_email,
            draft_ai_error=self.draft_ai_error,
        ).to_payload()


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    default_gmail_query: Mapped[str] = mapped_column(Text, default="is:unread in:inbox recruiter")
    saved_gmail_queries_json: Mapped[str] = mapped_column(Text, default="[]")
    mail_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    default_date_mode: Mapped[str] = mapped_column(String(20), default="today")
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accepted_locations: Mapped[str] = mapped_column(Text, default="")
    visa_required_allowed: Mapped[bool] = mapped_column(default=False)
    remote_preference: Mapped[str] = mapped_column(String(50), default="any")
    role_keywords: Mapped[str] = mapped_column(Text, default="")
    must_have_skills: Mapped[str] = mapped_column(Text, default="")
    employer_domains: Mapped[str] = mapped_column(Text, default="")
    free_text_guidance: Mapped[str] = mapped_column(Text, default="")
    qualification_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    feature_auto_polling: Mapped[bool] = mapped_column(default=False)
    feature_auto_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=10)
    feature_nvoids_enabled: Mapped[bool] = mapped_column(default=True)
    feature_nvoids_auto_sync: Mapped[bool] = mapped_column(default=False)
    feature_nvoids_poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    nvoids_batch_limit: Mapped[int] = mapped_column(Integer, default=10)
    nvoids_detail_title_mode: Mapped[str] = mapped_column(String(40), default="job_details")
    nvoids_locations: Mapped[str] = mapped_column(Text, default="")
    feature_auto_send: Mapped[bool] = mapped_column(default=False)
    feature_retry_queue: Mapped[bool] = mapped_column(default=False)
    feature_ai_enabled: Mapped[bool] = mapped_column(default=False)
    feature_ai_extractor_enabled: Mapped[bool] = mapped_column(default=False)
    feature_semantic_enabled: Mapped[bool] = mapped_column(default=False)
    feature_groq_job_parser_enabled: Mapped[bool] = mapped_column(default=False)
    feature_gmail_requirement_groups_enabled: Mapped[bool] = mapped_column(default=False)
    feature_role_manifest_enabled: Mapped[bool] = mapped_column(default=False)
    feature_strict_candidate_screening_enabled: Mapped[bool] = mapped_column(default=False)
    feature_email_tracking_enabled: Mapped[bool] = mapped_column(default=False)
    feature_reply_inbox_enabled: Mapped[bool] = mapped_column(default=False)
    candidate_work_authorizations_json: Mapped[str] = mapped_column(Text, default="[]")
    candidate_total_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_us_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    candidate_current_location: Mapped[str] = mapped_column(String(255), default="")
    draft_text_size: Mapped[str] = mapped_column(String(20), default="normal")
    fallback_draft_template: Mapped[str] = mapped_column(Text, default="")
    signature_name: Mapped[str] = mapped_column(String(255), default="")
    signature_phone: Mapped[str] = mapped_column(String(80), default="")
    signature_email: Mapped[str] = mapped_column(String(255), default="")
    preferred_employer_cc_email: Mapped[str] = mapped_column(String(255), default="")
    preferred_employer_cc_emails: Mapped[str] = mapped_column(Text, default="")
    default_employer_cc_emails: Mapped[str] = mapped_column(Text, default="")
    resume_display_name: Mapped[str] = mapped_column(String(255), default="")
    policy_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ResumeAsset(Base):
    __tablename__ = "resume_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/pdf")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    skills_text: Mapped[str] = mapped_column(Text, default="")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_current: Mapped[bool] = mapped_column(default=True)
    semantic_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class GmailRequirementGroup(Base):
    __tablename__ = "gmail_requirement_groups"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_group_email", name="ux_gmail_requirement_groups_owner_normalized"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    group_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    normalized_group_email: Mapped[str] = mapped_column(String(255), default="", index=True)
    group_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class AttachmentAsset(Base):
    __tablename__ = "attachment_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class CustomSkillTaxonomyEntry(Base):
    __tablename__ = "custom_skill_taxonomy_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    category: Mapped[str] = mapped_column(String(120), default="custom")
    cluster_hint: Mapped[str | None] = mapped_column(String(120), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    match_tier: Mapped[str] = mapped_column(String(40), default="supporting")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmailOpenEvent(Base):
    __tablename__ = "email_open_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    recruiter_email_id: Mapped[int] = mapped_column(Integer, index=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    user_agent: Mapped[str] = mapped_column(Text, default="")
    remote_ip: Mapped[str] = mapped_column(String(100), default="")
    is_likely_proxy: Mapped[bool] = mapped_column(Boolean, default=False)


class EmailConversation(Base):
    __tablename__ = "email_conversations"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_thread_id", name="ux_email_conversation_owner_thread"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    root_recruiter_email_id: Mapped[int] = mapped_column(Integer, index=True)
    external_thread_id: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(40), default="sent", index=True)
    last_message_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    unread_reply_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmailReplyMessage(Base):
    __tablename__ = "email_reply_messages"
    __table_args__ = (
        UniqueConstraint("owner_id", "external_message_id", name="ux_email_reply_owner_message"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    conversation_id: Mapped[int] = mapped_column(Integer, index=True)
    direction: Mapped[str] = mapped_column(String(20), default="inbound", index=True)
    external_message_id: Mapped[str] = mapped_column(String(255), index=True)
    external_rfc_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    in_reply_to_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    references_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    sender: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), default="default-owner", index=True)
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tool_call_args: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class CanonicalEntityTaxonomyEntry(Base):
    __tablename__ = "canonical_entity_taxonomy_entries"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "entity_type",
            "canonical_name",
            name="ux_canonical_entity_owner_type_name",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    canonical_name: Mapped[str] = mapped_column(String(255), index=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    occurrence_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class JobIntentTaxonomyEntry(Base):
    __tablename__ = "job_intent_taxonomy_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    phrase: Mapped[str] = mapped_column(String(255), index=True)
    normalized_phrase: Mapped[str] = mapped_column(String(255), index=True)
    polarity: Mapped[str] = mapped_column(String(80), index=True)
    source_examples_count: Mapped[int] = mapped_column(Integer, default=0)
    sample_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    confidence_aggregate: Mapped[float] = mapped_column(Float, default=0.0)
    last_intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    sync_batch_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecentRun(Base):
    __tablename__ = "recent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    run_source: Mapped[str] = mapped_column(String(40), index=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    sync_batch_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    external_scrape_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(40), default="ok")
    detail: Mapped[str] = mapped_column(Text, default="")
    matched_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queued_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_item_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    requirement_count: Mapped[int] = mapped_column(Integer, default=0)
    multi_role_source_count: Mapped[int] = mapped_column(Integer, default=0)
    manifest_review_count: Mapped[int] = mapped_column(Integer, default=0)
    job_backend_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    total_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    progress_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_name: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecentRunSkippedItem(Base):
    __tablename__ = "recent_run_skipped_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    run_source: Mapped[str] = mapped_column(String(40), index=True)
    run_key: Mapped[str] = mapped_column(String(160), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="gmail", index=True)
    outcome: Mapped[str] = mapped_column(String(40), default="skipped", index=True)
    reason_code: Mapped[str] = mapped_column(String(120), default="", index=True)
    reason_detail: Mapped[str] = mapped_column(Text, default="")
    external_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    external_thread_id: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    candidate_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    external_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    title_or_subject: Mapped[str] = mapped_column(String(500), default="")
    sender: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    gmail_message_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    intent_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    intent_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    intent_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    intent_negative_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    gate_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    gate_provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_group_match_method: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_group_trusted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    qualification_result: Mapped[str | None] = mapped_column(String(80), nullable=True)
    blocking_rule: Mapped[str | None] = mapped_column(String(120), nullable=True)
    qualification_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    qualification_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class DraftEditFeedback(Base):
    __tablename__ = "draft_edit_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_draft: Mapped[str] = mapped_column(Text)
    edited_draft: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


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
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class PremiumNumberLead(Base):
    __tablename__ = "premium_number_leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_email_id: Mapped[int] = mapped_column(Integer, index=True)
    phone_number_normalized: Mapped[str] = mapped_column(String(40), index=True)
    phone_number_display: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    purpose: Mapped[str] = mapped_column(String(255), default="Recruiter contact")
    confidence: Mapped[str] = mapped_column(String(10), default="low")
    contact_type: Mapped[str] = mapped_column(String(40), default="unknown")
    recruiter_relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    is_recruiter_relevant: Mapped[bool] = mapped_column(Boolean, default=False)
    relevance_reason: Mapped[str] = mapped_column(String(255), default="")
    source_fragment: Mapped[str] = mapped_column(Text, default="")
    source_email_sender: Mapped[str] = mapped_column(String(255), default="")
    source_email_subject: Mapped[str] = mapped_column(String(500), default="")
    source_email_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecruiterNumber(Base):
    __tablename__ = "recruiter_numbers"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_phone_number", name="ux_recruiter_numbers_owner_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    recruiter_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    recruiter_email: Mapped[str] = mapped_column(String(255), default="")
    first_detected_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class EmployerNumber(Base):
    __tablename__ = "employer_numbers"
    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_phone_number", name="ux_employer_numbers_owner_phone"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class RecruiterOpportunity(Base):
    __tablename__ = "recruiter_opportunities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "recruiter_number_id",
            "gmail_message_id",
            name="ux_recruiter_opportunities_owner_recruiter_msg",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    recruiter_number_id: Mapped[int] = mapped_column(Integer, index=True)
    source_email_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gmail_message_id: Mapped[str] = mapped_column(String(255), index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="gmail", index=True)
    source_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    external_opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    email_sender: Mapped[str] = mapped_column(String(255), default="")
    gmail_open_url: Mapped[str] = mapped_column(String(1000), default="")
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    job_title: Mapped[str] = mapped_column(String(255), default="")
    client: Mapped[str] = mapped_column(String(255), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    work_mode: Mapped[str] = mapped_column(String(80), default="")
    visa_restrictions: Mapped[str] = mapped_column(String(255), default="")
    extracted_skills: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="New")
    notes: Mapped[str] = mapped_column(Text, default="")
    cold_call_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    cold_call_script_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class NumberReviewQueue(Base):
    __tablename__ = "number_review_queue"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "normalized_phone_number",
            "source_email_id",
            name="ux_number_review_queue_owner_phone_email",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    source_email_id: Mapped[int] = mapped_column(Integer, index=True)
    normalized_phone_number: Mapped[str] = mapped_column(String(40), index=True)
    display_phone_number: Mapped[str] = mapped_column(String(80))
    owner_name: Mapped[str] = mapped_column(String(255), default="Unknown")
    company: Mapped[str] = mapped_column(String(255), default="Unknown")
    designation: Mapped[str] = mapped_column(String(255), default="Unknown")
    confidence: Mapped[str] = mapped_column(String(10), default="low")
    purpose: Mapped[str] = mapped_column(String(255), default="")
    evidence_snippet: Mapped[str] = mapped_column(Text, default="")
    email_subject: Mapped[str] = mapped_column(String(500), default="")
    email_sender: Mapped[str] = mapped_column(String(255), default="")
    gmail_open_url: Mapped[str] = mapped_column(String(1000), default="")
    state: Mapped[str] = mapped_column(String(40), default="pending")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class ProductivityEvent(Base):
    __tablename__ = "productivity_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_source: Mapped[str] = mapped_column(String(40), default="system")
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


# Register external feed models on shared Base metadata for test create_all flows.
from app.external_feeds.models import ExternalFeedSource, ExternalOpportunity, ExternalScrapeRun  # noqa: E402,F401
