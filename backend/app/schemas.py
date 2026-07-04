from datetime import datetime
import json
import re
from typing import Any, cast

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.ai.draft_formatting import DRAFT_TEXT_SIZE_VALUES, normalize_draft_text_size


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


class RegenerateCandidateRequest(BaseModel):
    preserve_manual_routing: bool = True
    preserve_review_visibility: bool = True


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
    saved_gmail_queries: list[str] = Field(default_factory=list)
    mail_date: str | None = None
    default_date_mode: str = "today"
    min_salary: int | None = None
    accepted_locations: list[str] = Field(default_factory=list)
    visa_required_allowed: bool = False
    remote_preference: str = "any"
    role_keywords: list[str] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    employer_domains: list[str] = Field(default_factory=list)
    free_text_guidance: str = ""
    qualification_threshold: float = 0.6
    feature_auto_polling: bool = False
    feature_auto_poll_interval_minutes: int = 10
    feature_nvoids_enabled: bool = True
    feature_nvoids_auto_sync: bool = False
    feature_nvoids_poll_interval_minutes: int = 30
    nvoids_batch_limit: int = 10
    nvoids_locations: list[str] = Field(default_factory=list)
    feature_auto_send: bool = False
    feature_retry_queue: bool = False
    feature_ai_enabled: bool = False
    feature_ai_extractor_enabled: bool = False
    feature_semantic_enabled: bool = False
    feature_groq_job_parser_enabled: bool = False
    draft_text_size: str = "normal"
    fallback_draft_template: str = ""
    signature_name: str = ""
    signature_phone: str = ""
    signature_email: str = ""
    preferred_employer_cc_email: str = ""
    resume_display_name: str = ""
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

    @field_validator("feature_nvoids_poll_interval_minutes")
    @classmethod
    def validate_nvoids_poll_interval(cls, value: int) -> int:
        return max(1, min(int(value), 1440))

    @field_validator("nvoids_batch_limit")
    @classmethod
    def validate_nvoids_batch_limit(cls, value: int) -> int:
        return max(1, min(int(value), 50))

    @field_validator("draft_text_size")
    @classmethod
    def validate_draft_text_size(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in DRAFT_TEXT_SIZE_VALUES:
            raise ValueError("draft_text_size must be one of: small, normal, large, huge")
        return normalize_draft_text_size(normalized)

    @field_validator("preferred_employer_cc_email")
    @classmethod
    def validate_preferred_employer_cc_email(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if not normalized:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", normalized):
            raise ValueError("preferred_employer_cc_email must be a valid email address")
        return normalized


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
    skills_text: str
    is_enabled: bool
    is_current: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResumeUpdateRequest(BaseModel):
    is_enabled: bool | None = None
    skills_text: str | None = None


class AttachmentAssetResponse(BaseModel):
    id: int
    owner_id: str
    file_name: str
    mime_type: str
    sha256: str
    file_size: int
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AttachmentAssetUpdateRequest(BaseModel):
    is_enabled: bool


class PendingSkillResponse(BaseModel):
    skill_name: str
    normalized_name: str
    occurrence_count: int
    candidate_ids: list[int] = Field(default_factory=list)


class CustomSkillTaxonomyEntryResponse(BaseModel):
    id: int
    owner_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list, validation_alias=AliasChoices("aliases", "aliases_json"))
    category: str
    cluster_hint: str | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("aliases", mode="before")
    @classmethod
    def parse_aliases(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class ApproveSkillRequest(BaseModel):
    skill_name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    category: str = "custom"
    cluster_hint: str | None = None


class DismissSkillRequest(BaseModel):
    skill_name: str
    canonical_name: str | None = None


class JobIntentTaxonomyEntryResponse(BaseModel):
    id: int
    owner_id: str
    phrase: str
    normalized_phrase: str
    polarity: str
    source_examples_count: int
    sample_evidence: list[str] = Field(default_factory=list, validation_alias=AliasChoices("sample_evidence", "sample_evidence_json"))
    confidence_aggregate: float
    last_intent_type: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("sample_evidence", mode="before")
    @classmethod
    def parse_sample_evidence(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class ApproveJobIntentSignalRequest(BaseModel):
    phrase: str
    polarity: str


class DismissJobIntentSignalRequest(BaseModel):
    phrase: str
    polarity: str


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
    ats_score: float | None = None
    ats_score_source: str | None = None
    ats_summary: str | None = None
    ats_breakdown: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("ats_breakdown", "ats_breakdown_json"),
    )
    semantic_input_source: str | None = None
    semantic_input_chars: int | None = None
    semantic_chunks: int | None = None
    semantic_fallback_reason: str | None = None
    keyword_source: str | None = None
    thread_snapshot_used: bool | None = None
    thread_snapshot_email_id: int | None = None
    skip_reason: str | None
    intent_type: str | None = None
    intent_confidence: float | None = None
    intent_reason: str | None = None
    intent_evidence: list[str] = Field(default_factory=list, validation_alias=AliasChoices("intent_evidence", "intent_evidence_json"))
    intent_negative_evidence: list[str] = Field(default_factory=list, validation_alias=AliasChoices("intent_negative_evidence", "intent_negative_evidence_json"))
    gate_action: str | None = None
    gate_provider: str | None = None
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
    applied_gmail_label: str | None = None
    applied_gmail_label_id: str | None = None
    applied_gmail_label_at: datetime | None = None
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
    parser_details: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("parser_details", "parser_details_json"),
    )
    attachment_file_names: list[str] = Field(default_factory=list)
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

    @field_validator("intent_evidence", "intent_negative_evidence", mode="before")
    @classmethod
    def parse_intent_lists(cls, value: Any) -> list[str]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    @field_validator("parser_details", mode="before")
    @classmethod
    def parse_parser_details(cls, value: Any) -> dict[str, object] | None:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        return None

    @field_validator("ats_breakdown", mode="before")
    @classmethod
    def parse_ats_breakdown(cls, value: Any) -> dict[str, object] | None:
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return None
            return cast(dict[str, object], parsed) if isinstance(parsed, dict) else None
        if isinstance(value, dict):
            return cast(dict[str, object], value)
        return None


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
    groq_configured: bool = False
    groq_enabled_in_settings: bool = False
    groq_model: str = ""
    groq_base_url_present: bool = False
    groq_runtime_healthy: bool | None = None
    groq_last_error: str | None = None
    groq_detail: str = ""
    groq_request_mode: str = ""
    groq_last_attempted_at: datetime | None = None
    groq_last_success_at: datetime | None = None
    groq_last_duration_ms: int | None = None
    semantic_input_source: str | None = None
    semantic_input_chars: int | None = None
    semantic_chunks: int | None = None
    semantic_fallback_reason: str | None = None
    keyword_source: str | None = None
    thread_snapshot_used: bool | None = None
    thread_snapshot_email_id: int | None = None
    last_error: str | None = None
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    last_duration_ms: int | None = None
    last_draft_source: str | None = None


class GmailSyncResponse(BaseModel):
    sync_batch_id: str
    run_key: str | None = None
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


class SentItemDetailsResponse(BaseModel):
    email_id: int
    source_type: str
    source_label: str
    requirement_received_link: str | None = None
    sent_gmail_message_link: str | None = None
    resume_variant_sent: str | None = None
    attached_files: list[str] = Field(default_factory=list)
    company: str | None = None
    recruiter_name: str | None = None
    recruiter_email: str | None = None
    recruiter_phone: str | None = None
    end_client: str | None = None
    implementation_partner: str | None = None
    vendor: str | None = None
    domain_mentioned: str | None = None
    experience_required: str | None = None
    mandatory_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    ats_score: float | None = None
    ats_summary: str | None = None
    to_email: str | None = None
    cc_email: str | None = None
    sent_at: datetime | None = None


class PremiumNumberResponse(BaseModel):
    id: int
    recruiter_email_id: int
    phone_number_display: str
    phone_number_normalized: str
    owner_name: str
    company: str
    designation: str
    purpose: str
    confidence: str
    contact_type: str
    recruiter_relevance_score: int
    is_recruiter_relevant: bool
    relevance_reason: str
    source_fragment: str
    source_email_sender: str
    source_email_subject: str
    source_email_message_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PremiumNumberListResponse(BaseModel):
    items: list[PremiumNumberResponse]
    next_cursor: int | None
    has_next: bool


class UnknownNumberReviewCardResponse(BaseModel):
    id: int
    source_email_id: int
    normalized_phone_number: str
    display_phone_number: str
    owner_name: str
    company: str
    designation: str
    confidence: str
    purpose: str
    evidence_snippet: str
    email_subject: str
    email_sender: str
    gmail_open_url: str
    state: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class UnknownNumberReviewCardListResponse(BaseModel):
    items: list[UnknownNumberReviewCardResponse]
    next_cursor: int | None
    has_next: bool


class RecruiterNumberResponse(BaseModel):
    id: int
    normalized_phone_number: str
    display_phone_number: str
    recruiter_name: str
    company: str
    designation: str
    recruiter_email: str
    first_detected_email_id: int | None
    total_opportunity_count: int = 0
    last_email_received_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class EmployerNumberResponse(BaseModel):
    id: int
    normalized_phone_number: str
    display_phone_number: str
    owner_name: str
    company: str
    source_email_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecruiterNumberListResponse(BaseModel):
    items: list[RecruiterNumberResponse]
    next_cursor: int | None
    has_next: bool


class EmployerNumberListResponse(BaseModel):
    items: list[EmployerNumberResponse]
    next_cursor: int | None
    has_next: bool


class RecruiterOpportunityResponse(BaseModel):
    id: int
    recruiter_number_id: int
    source_email_id: int | None
    gmail_message_id: str
    source_type: str = "gmail"
    source_url: str | None = None
    external_opportunity_id: int | None = None
    email_subject: str
    email_sender: str
    gmail_open_url: str
    received_at: datetime | None
    job_title: str
    client: str
    location: str
    work_mode: str
    visa_restrictions: str
    extracted_skills: str
    evidence: str
    recruiter_name: str = ""
    recruiter_email: str = ""
    recruiter_phone_display: str = ""
    recruiter_phone_normalized: str = ""
    status: str
    notes: str
    cold_call_script: str | None = None
    cold_call_script_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecruiterOpportunityListResponse(BaseModel):
    items: list[RecruiterOpportunityResponse]
    next_cursor: int | None
    has_next: bool


class RecruiterOpportunityPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None


class RecruiterOpportunityDeleteResponse(BaseModel):
    id: int
    deleted: bool
    recruiter_number_deleted: bool


class ExternalFeedSyncResponse(BaseModel):
    source_type: str
    run_key: str | None = None
    fetched_count: int
    created_count: int
    deduped_count: int
    failed_count: int
    skipped_location_count: int
    run_id: int


class ExternalScrapeRunResponse(BaseModel):
    id: int
    source_type: str
    started_at: datetime
    ended_at: datetime | None
    fetched_count: int
    created_count: int
    deduped_count: int
    failed_count: int
    notes: str


class AutomationRunResponse(BaseModel):
    status: str
    detail: str
    run_key: str | None = None
    email_id: int | None = None
    gmail_message_url: str | None = None
    decision_reason: str | None = None
    skip_reason: str | None = None
    routing_reason: str | None = None
    applied_gmail_label: str | None = None
    applied_gmail_label_id: str | None = None
    effective_query: str | None = None
    matched_count: int | None = None
    queued_count: int | None = None
    skipped_count: int | None = None
    failed_count: int | None = None
    auto_sent_count: int | None = None
    auto_send_failed_count: int | None = None
    retry_promoted_count: int | None = None
    retry_skipped_count: int | None = None


class RecentRunItemResponse(BaseModel):
    id: int
    run_key: str
    run_source: str
    source_type: str
    outcome: str
    reason_code: str
    reason_detail: str
    external_message_id: str | None = None
    external_thread_id: str | None = None
    candidate_email_id: int | None = None
    external_opportunity_id: int | None = None
    title_or_subject: str
    sender: str
    location: str | None = None
    source_url: str | None = None
    gmail_message_url: str | None = None
    intent_type: str | None = None
    intent_confidence: float | None = None
    intent_reason: str | None = None
    intent_evidence: list[str] = Field(default_factory=list)
    intent_negative_evidence: list[str] = Field(default_factory=list)
    gate_action: str | None = None
    gate_provider: str | None = None
    created_at: datetime


class RecentRunItemListResponse(BaseModel):
    items: list[RecentRunItemResponse]
    next_cursor: int | None
    has_next: bool


class RecentRunResponse(BaseModel):
    run_key: str
    run_source: str
    status: str
    detail: str
    matched_count: int | None = None
    queued_count: int | None = None
    skipped_count: int | None = None
    failed_count: int | None = None
    skipped_item_count: int = 0
    sync_batch_id: str | None = None
    external_scrape_run_id: int | None = None
    created_at: datetime


class RecentRunListResponse(BaseModel):
    items: list[RecentRunResponse]
    next_cursor: int | None = None
    has_next: bool = False


class AutomationRunRequest(BaseModel):
    mail_date: str | None = None

    @field_validator("mail_date")
    @classmethod
    def validate_mail_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        datetime.strptime(value, "%Y-%m-%d")
        return value


class GmailLabelingPreviewRequest(BaseModel):
    sender: str
    subject: str
    body: str
    state: str = "needs_review"
    decision: str = "Qualified"
    routing_status: str = "unverified"
    routing_confidence: float = 0.0
    skip_reason: str | None = None
    draft_reply: str = ""


class GmailLabelingPreviewResponse(BaseModel):
    label: str
    reason_path: str


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
