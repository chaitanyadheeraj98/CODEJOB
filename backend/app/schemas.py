from datetime import datetime
import json
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator


class IngestEmailRequest(BaseModel):
    sender: str
    subject: str
    body: str


class ApproveSendRequest(BaseModel):
    edited_reply: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


class BulkRejectRequest(BaseModel):
    ids: list[int]
    reason: str | None = None


class ResolveRecipientsRequest(BaseModel):
    to_email: str
    cc_email: str


class RoutingEvidenceResponse(BaseModel):
    role: str
    email: str
    source: str
    detail: str


RoutingItemDict = dict[str, object]
RoutingListInput = list[RoutingItemDict] | list[RoutingEvidenceResponse]
PolicyDict = dict[str, Any]


class SettingsRequest(BaseModel):
    enabled: bool = True
    gmail_query: str = "is:unread in:inbox recruiter"
    default_gmail_query: str = "is:unread in:inbox recruiter"
    mail_date: str | None = None
    default_date_mode: str = "today"
    min_salary: int | None = None
    accepted_locations: list[str] = Field(default_factory=list)
    visa_required_allowed: bool = False
    remote_preference: str = "any"
    role_keywords: list[str] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    free_text_guidance: str = ""
    qualification_threshold: float = 0.6
    feature_auto_polling: bool = False
    feature_auto_poll_interval_minutes: int = 10
    feature_auto_send: bool = False
    feature_retry_queue: bool = False
    feature_ai_enabled: bool = False
    feature_semantic_enabled: bool = False
    fallback_draft_template: str = ""
    signature_name: str = ""
    signature_phone: str = ""
    signature_email: str = ""
    policy: PolicyDict | None = None

    @field_validator("mail_date")
    @classmethod
    def validate_mail_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        datetime.strptime(value, "%Y-%m-%d")
        return value

    @field_validator("default_date_mode")
    @classmethod
    def validate_default_date_mode(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"today", "off"}:
            raise ValueError("default_date_mode must be 'today' or 'off'")
        return normalized

    @field_validator("feature_auto_poll_interval_minutes")
    @classmethod
    def validate_poll_interval(cls, value: int) -> int:
        return max(1, min(int(value), 1440))


class SettingsResponse(SettingsRequest):
    policy_profile_options: list[str] | None = None
    policy_profile_selected: str | None = None
    owner_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResumeResponse(BaseModel):
    id: int
    owner_id: str
    file_name: str
    mime_type: str
    sha256: str
    version: int
    is_current: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DraftQualityResponse(BaseModel):
    content_valid: bool
    greeting_compliance: str
    resume_context_status: str
    confidence: float
    score: int
    label: str
    issues: list[str] = Field(default_factory=list)


class EmailResponse(BaseModel):
    id: int
    owner_id: str
    sender: str
    subject: str
    body: str
    role: str
    location: str
    salary_text: str
    skills_text: str
    score: int
    decision: str
    state: str
    decision_reason: str | None
    hard_filter_result: str | None
    auto_reject_reason: str | None
    ai_score: float | None
    ai_score_source: str | None
    ai_summary: str | None
    skip_reason: str | None
    sync_batch_id: str | None
    draft_reply: str
    draft_source: str | None = None
    draft_model: str | None = None
    draft_ai_error: str | None = None
    draft_resume_context_status: str | None = None
    draft_quality: DraftQualityResponse | None = None
    approval_status: str
    sent_status: str
    source: str
    external_message_id: str | None
    external_thread_id: str | None
    external_rfc_message_id: str | None
    gmail_received_at: datetime | None
    gmail_message_url: str | None = None
    recipient_email: str | None
    cc_email: str | None
    routing_status: str
    routing_confidence: float
    routing_reason: str
    routing_evidence: list[RoutingEvidenceResponse] = Field(default_factory=list)
    routing_candidates: list[RoutingEvidenceResponse] = Field(default_factory=list)
    routing_confirmed: bool
    resume_asset_id: int | None
    resume_file_name: str | None
    sent_at: datetime | None
    gmail_sent_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("routing_evidence", "routing_candidates", mode="before")
    @classmethod
    def parse_routing_json(cls, value: Any) -> RoutingListInput:
        empty_list: list[RoutingItemDict] = []
        if value in (None, ""):
            return empty_list
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return empty_list
            if isinstance(parsed, list):
                return cast(list[RoutingItemDict], parsed)
            return empty_list
        if isinstance(value, list):
            return cast(RoutingListInput, value)
        return empty_list


class GmailStatusResponse(BaseModel):
    configured: bool
    authenticated: bool
    token_path: str
    last_sync_at: datetime | None
    detail: str


class AIStatusResponse(BaseModel):
    configured: bool
    connected: bool
    running: bool
    provider: str
    model: str
    detail: str
    embedding_provider: str
    embedding_model: str
    embedding_connected: bool
    embedding_detail: str
    embedding_configured: bool | None = None
    embedding_runtime_healthy: bool | None = None
    embedding_last_error: str | None = None
    embedding_last_attempted_at: datetime | None = None
    embedding_last_success_at: datetime | None = None
    embedding_last_duration_ms: int | None = None
    last_error: str | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_duration_ms: int | None = None
    last_draft_source: str | None = None


class GmailSyncResponse(BaseModel):
    sync_batch_id: str
    imported_count: int
    skipped_count: int
    error_count: int


class OAuthStartResponse(BaseModel):
    status: str
    detail: str
    configured: bool
    authenticated: bool
    authorization_url: str | None = None


class OAuthUrlResponse(BaseModel):
    authorization_url: str | None = None


class CandidateListResponse(BaseModel):
    items: list[EmailResponse]
    next_cursor: int | None
    has_next: bool


class AutomationRunResponse(BaseModel):
    status: str
    detail: str
    email_id: int | None = None
    gmail_message_url: str | None = None
    decision_reason: str | None = None
    skip_reason: str | None = None
    routing_reason: str | None = None
    effective_query: str | None = None
    matched_count: int | None = None
    queued_count: int | None = None
    skipped_count: int | None = None
    failed_count: int | None = None


class AutomationRunRequest(BaseModel):
    mail_date: str | None = None

    @field_validator("mail_date")
    @classmethod
    def validate_mail_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        datetime.strptime(value, "%Y-%m-%d")
        return value


class TelegramStatusResponse(BaseModel):
    enabled: bool
    polling: bool
    alerts_enabled: bool
    authorized_chats: int
    detail: str


class ProductivityEventCreateRequest(BaseModel):
    event_type: str
    event_source: str = "ui"
    entity_id: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductivityEventResponse(BaseModel):
    id: int
    owner_id: str
    event_type: str
    event_source: str
    entity_id: int | None
    weight: float
    metadata: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    created_at: datetime


class ProductivityBarPoint(BaseModel):
    ts: datetime
    sent_count: int
    failed_count: int = 0
    needs_review_count: int = 0
    recent_run_count: int = 0


class ProductivityTrendResponse(BaseModel):
    range: str
    bucket: str
    trend_direction: str
    trend_delta_pct: float
    kpi_total_sent: int = 0
    previous_period_total_sent: int = 0
    bars: list[ProductivityBarPoint] = Field(default_factory=list)
