from datetime import datetime
import json
import re
from typing import Any, Literal, cast
from zoneinfo import available_timezones

from pydantic import AliasChoices, BaseModel, Field, field_validator

from app.ai.draft_formatting import DRAFT_TEXT_SIZE_VALUES, normalize_draft_text_size


class IngestEmailRequest(BaseModel):
    sender: str
    subject: str
    body: str


class ApproveSendRequest(BaseModel):
    edited_reply: str | None = None
    confirm_same_source_additional_send: bool = False


class RejectRequest(BaseModel):
    reason: str | None = None


class BulkRejectRequest(BaseModel):
    ids: list[int] = Field(max_length=25)
    reason: str | None = None


class BulkApproveRequest(BaseModel):
    ids: list[int] = Field(max_length=25)
    edited_replies: dict[int, str] | None = None
    idempotency_key: str | None = Field(default=None, max_length=64)


class BulkRegenerateRequest(BaseModel):
    ids: list[int] = Field(max_length=25)


class BulkSendToFailedMappingRequest(BaseModel):
    ids: list[int] = Field(max_length=25)


class BulkTrackRequest(BaseModel):
    ids: list[int] = Field(max_length=25)
    tracked: bool


class BulkResolveRecipientsRequest(BaseModel):
    fixes: dict[int, "ResolveRecipientsRequest"] = Field(max_length=25)


class BulkDeleteCandidatesRequest(BaseModel):
    ids: list[int] = Field(max_length=25)


class BulkCandidateActionResponse(BaseModel):
    succeeded_ids: list[int]
    failed: list[dict[str, object]]


class ManualPremiumContactRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    title: str = Field(default="", max_length=255)
    company: str = Field(default="", max_length=255)
    email: str = Field(default="", max_length=255)
    phone: str = Field(default="", max_length=80)
    role: Literal["recruiter", "employer"] = "recruiter"


class ChatSendReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    subject: str | None = Field(default=None, max_length=998)


class GithubIssueCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    user_report: str = Field(min_length=1, max_length=4000)
    ai_summary: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=4000)


class ResolveRecipientsRequest(BaseModel):
    to_email: str
    cc_email: str


class RegenerateCandidateRequest(BaseModel):
    preserve_manual_routing: bool = True
    preserve_review_visibility: bool = True
    allow_role_manifest_fork: bool = False


class RoleDetectionRetryResponse(BaseModel):
    source_parent_id: int
    manifest_status: str
    requirement_count: int
    child_ids: list[int] = Field(default_factory=list)


class RoutingEvidenceResponse(BaseModel):
    role: str
    email: str
    source: str
    detail: str


RoutingItemDict = dict[str, object]
RoutingListInput = list[RoutingItemDict] | list[RoutingEvidenceResponse]
PolicyDict = dict[str, Any]


def _validate_visible_filters(value: dict[str, list[str]]) -> dict[str, list[str]]:
    if len(value) > 40:
        raise ValueError("visible_filters may contain at most 40 dashboard keys")
    normalized: dict[str, list[str]] = {}
    for dashboard_key, field_keys in value.items():
        if len(dashboard_key) > 80:
            raise ValueError("visible_filters keys may contain at most 80 characters")
        if len(field_keys) > 60:
            raise ValueError("visible_filters dashboards may contain at most 60 fields")
        unique: list[str] = []
        for field_key in field_keys:
            if len(field_key) > 80:
                raise ValueError("visible_filters field keys may contain at most 80 characters")
            if field_key not in unique:
                unique.append(field_key)
        normalized[dashboard_key] = unique
    return normalized


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
    nvoids_detail_title_mode: str = "job_details"
    nvoids_locations: list[str] = Field(default_factory=list)
    nvoids_job_role: str = ""
    nvoids_search_location: str = ""
    nvoids_custom_query: str = ""
    feature_auto_send: bool = False
    feature_retry_queue: bool = False
    feature_ai_enabled: bool = False
    feature_ai_extractor_enabled: bool = False
    feature_semantic_enabled: bool = False
    feature_groq_job_parser_enabled: bool = False
    feature_gmail_requirement_groups_enabled: bool = False
    feature_role_manifest_enabled: bool = False
    feature_strict_candidate_screening_enabled: bool = False
    feature_email_tracking_enabled: bool = False
    feature_reply_inbox_enabled: bool = False
    feature_applications_enabled: bool = False
    feature_application_automation_enabled: bool = False
    feature_application_outreach_drafts_enabled: bool = False
    feature_reminder_sweep_interval_minutes: int = 240
    feature_resume_tracking_enabled: bool = False
    feature_resume_tracking_sweep_interval_minutes: int = 240
    candidate_work_authorizations: list[str] | None = Field(default_factory=list)
    preferred_employment_types: list[Literal["C2C", "W2", "1099", "FT"]] = Field(default_factory=list)
    visible_filters: dict[str, list[str]] = Field(default_factory=dict)
    preferred_minimum_rate: float | None = Field(default=None, ge=0)
    candidate_total_experience_years: float | None = Field(default=None, ge=0)
    candidate_us_experience_years: float | None = Field(default=None, ge=0)
    candidate_current_location: str | None = ""
    draft_text_size: str = "normal"
    fallback_draft_template: str = ""
    signature_name: str = ""
    signature_phone: str = ""
    signature_email: str = ""
    preferred_employer_cc_emails: list[str] = Field(default_factory=list)
    default_employer_cc_emails: list[str] = Field(default_factory=list)
    # One-release compatibility for clients that still send the singular field.
    preferred_employer_cc_email: str = ""
    resume_display_name: str = ""
    policy: PolicyDict | None = None
    timezone: str = "UTC"
    feature_scheduling_sweep_interval_minutes: int = 15

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        name = (value or "").strip() or "UTC"
        # available_timezones() needs tzdata on Windows and in slim containers.
        # It is already a declared dependency and, until now, never imported.
        if name not in available_timezones():
            raise ValueError("Unknown timezone")
        return name

    # Deliberately NOT added to validate_reminder_sweep_interval's tuple: that
    # would loosen two shipped sweeps from a 30-minute floor to 5 as a side
    # effect. Scheduling needs a 5-minute floor of its own, because a 30-minute
    # floor makes a reminder set for 09:15 arrive as late as 09:45.
    @field_validator("feature_scheduling_sweep_interval_minutes")
    @classmethod
    def validate_scheduling_sweep_interval(cls, value: int) -> int:
        return max(5, min(int(value), 1440))

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

    @field_validator(
        "feature_reminder_sweep_interval_minutes",
        "feature_resume_tracking_sweep_interval_minutes",
    )
    @classmethod
    def validate_reminder_sweep_interval(cls, value: int) -> int:
        return max(30, min(int(value), 1440))

    @field_validator("nvoids_batch_limit")
    @classmethod
    def validate_nvoids_batch_limit(cls, value: int) -> int:
        return max(1, min(int(value), 50))

    @field_validator("nvoids_detail_title_mode")
    @classmethod
    def validate_nvoids_detail_title_mode(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if normalized not in {"job_details", "hotlist_details", "all"}:
            raise ValueError("nvoids_detail_title_mode must be one of: job_details, hotlist_details, all")
        return normalized

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

    @field_validator("preferred_employer_cc_emails", "default_employer_cc_emails")
    @classmethod
    def validate_employer_cc_emails(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            email = (value or "").strip().lower()
            if not re.fullmatch(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email):
                raise ValueError("employer CC entries must be valid email addresses")
            if email not in normalized:
                normalized.append(email)
        return normalized

    @field_validator("visible_filters")
    @classmethod
    def validate_visible_filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return _validate_visible_filters(value)


class VisibleFiltersRequest(BaseModel):
    visible_filters: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("visible_filters")
    @classmethod
    def validate_visible_filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        return _validate_visible_filters(value)


class SettingsResponse(SettingsRequest):
    policy_profile_options: list[str] | None = None
    policy_profile_selected: str | None = None
    owner_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FilterOptionsResponse(BaseModel):
    bucket: str
    field: str
    values: list[str] = Field(default_factory=list)


class ResumeResponse(BaseModel):
    id: int
    owner_id: str
    file_name: str
    mime_type: str
    sha256: str
    version: int
    skills_text: str
    primary_role: str = ""
    structured_skills: list[str] = Field(default_factory=list)
    variant_label: str = ""
    is_enabled: bool
    is_current: bool
    content_summary: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResumeUpdateRequest(BaseModel):
    is_enabled: bool | None = None
    skills_text: str | None = None
    primary_role: str | None = None
    structured_skills: list[str] | None = None
    variant_label: str | None = None


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
    suspicious: bool = False
    recoverable_skills: list[str] = Field(default_factory=list)
    source_tags: list[str] = Field(default_factory=list)


class BulkApproveSkillsResponse(BaseModel):
    processed_count: int
    approved_count: int
    skipped_count: int
    approved_skill_names: list[str] = Field(default_factory=list)


class EmbeddingStatusResponse(BaseModel):
    pending_count: int


class EmbedPendingSkillsResponse(BaseModel):
    embedded_count: int
    remaining_count: int
    duration_ms: int


class PendingEntityResponse(BaseModel):
    entity_type: str
    display_name: str
    normalized_name: str
    occurrence_count: int
    candidate_ids: list[int] = Field(default_factory=list)


class CanonicalEntityTaxonomyEntryResponse(BaseModel):
    id: int
    owner_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list, validation_alias=AliasChoices("aliases", "aliases_json"))
    occurrence_count: int
    embedding_status: str
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
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []


class ApproveEntityRequest(BaseModel):
    display_name: str
    canonical_name: str | None = None
    aliases: list[str] = Field(default_factory=list)


class DismissEntityRequest(BaseModel):
    display_name: str


class BulkApproveEntitiesResponse(BaseModel):
    processed_count: int
    approved_count: int
    skipped_count: int
    approved_names: list[str] = Field(default_factory=list)


class TaxonomyMetricsResponse(BaseModel):
    parsed_email_count: int
    emails_with_unknown_skills: int
    unknown_skill_rate: float
    pending_skill_count: int
    pending_company_count: int
    pending_location_count: int
    alias_collision_count: int


class BulkApproveJobIntentSignalItemResponse(BaseModel):
    phrase: str
    polarity: str


class BulkApproveJobIntentSignalsResponse(BaseModel):
    processed_count: int
    approved_count: int
    skipped_count: int
    approved_signals: list[BulkApproveJobIntentSignalItemResponse] = Field(default_factory=list)


class CustomSkillTaxonomyEntryResponse(BaseModel):
    id: int
    owner_id: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list, validation_alias=AliasChoices("aliases", "aliases_json"))
    category: str
    cluster_hint: str | None
    description: str = ""
    weight: float = 1.0
    match_tier: str = "supporting"
    occurrence_count: int = 0
    embedding_status: str = "pending"
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


class EmbeddedJobIntentSignalResponse(BaseModel):
    id: int
    phrase: str
    polarity: str
    confidence: float
    embedded: bool


class SettingsBootstrapResponse(BaseModel):
    settings: SettingsResponse
    role_manifest_child_creation_enabled: bool = False
    gmail_requirement_groups: list["GmailRequirementGroupResponse"] = Field(default_factory=list)
    resumes: list[ResumeResponse] = Field(default_factory=list)
    attachments: list[AttachmentAssetResponse] = Field(default_factory=list)
    pending_skills: list[PendingSkillResponse] = Field(default_factory=list)
    pending_job_intent_signals: list[JobIntentTaxonomyEntryResponse] = Field(default_factory=list)
    approved_job_intent_signals: list[JobIntentTaxonomyEntryResponse] = Field(default_factory=list)
    loaded_at: datetime
    owner_id: str


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
    # NULL for every row written before provenance existed - treat as unverified,
    # never as "extracted". See services/role_provenance.py.
    role_source: str | None = None
    role_canonical: str | None = None
    location: str
    location_source: str | None = None
    company: str | None = None
    company_source: str | None = None
    end_client: str | None = None
    implementation_partner: str | None = None
    domain: str | None = None
    domain_confidence: str | None = None
    interview_type: str | None = None
    salary_text: str
    skills_text: str
    score: int
    decision: str
    state: str
    marked_for_tracking: bool = False
    premium_status: str | None = None
    premium_verification_level: str | None = None
    following_badge: Literal["bookmarked", "tracked", "active"] | None = None
    following_warning: str | None = None
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
    resume_picker_score: float | None = None
    resume_picker_reason: str | None = None
    resume_picker_candidates: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("resume_picker_candidates", "resume_picker_candidates_json"),
    )
    resume_picker_breakdown: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("resume_picker_breakdown", "resume_picker_breakdown_json"),
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
    gate_error: str | None = None
    source_group_name: str | None = None
    source_group_email: str | None = None
    source_group_match_method: str | None = None
    source_group_trusted: bool | None = None
    qualification_result: str | None = None
    blocking_rule: str | None = None
    qualification_detail: str | None = None
    qualification_context: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("qualification_context", "qualification_context_json"),
    )
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
    screening_mode: str | None = None
    source_parent_email_id: int | None = None
    is_source_parent: bool = False
    is_multi_role_child: bool = False
    requirement_index: int | None = None
    requirement_count: int | None = None
    requirement_key: str | None = None
    requirement_source_text: str | None = None
    inherited_constraints: list[dict[str, object]] = Field(
        default_factory=list,
        validation_alias=AliasChoices("inherited_constraints", "inherited_constraints_json"),
    )
    role_manifest_status: str = "not_run"
    role_manifest_confidence: float | None = None
    role_manifest: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("role_manifest", "role_manifest_json"),
    )
    role_manifest_diagnostics: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("role_manifest_diagnostics", "role_manifest_diagnostics_json"),
    )
    eligibility_status: str | None = None
    eligibility_details: dict[str, object] | None = Field(
        default=None,
        validation_alias=AliasChoices("eligibility_details", "eligibility_details_json"),
    )
    sendability_status: str | None = None
    attachment_file_names: list[str] = Field(default_factory=list)
    sent_at: datetime | None
    gmail_sent_id: str | None
    last_error: str | None
    record_id: str | None = None
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

    @field_validator("parser_details", "ats_breakdown", "resume_picker_candidates", "resume_picker_breakdown", "qualification_context", "role_manifest", "role_manifest_diagnostics", "eligibility_details", mode="before")
    @classmethod
    def parse_json_object(cls, value: Any) -> dict[str, object] | None:
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

    @field_validator("inherited_constraints", mode="before")
    @classmethod
    def parse_json_list(cls, value: Any) -> list[dict[str, object]]:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        return [cast(dict[str, object], item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


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
    # Provider-neutral intent-gate status. Added alongside the groq_* fields rather
    # than replacing them so the AI Access card can be relabelled without a
    # breaking API change, and so Groq stays observable while it is the rollback.
    intent_gate_provider: str = ""
    intent_gate_model: str = ""
    intent_gate_configured: bool = False
    intent_gate_enabled_in_settings: bool = False
    intent_gate_runtime_healthy: bool | None = None
    intent_gate_last_error: str | None = None
    intent_gate_detail: str = ""
    intent_gate_effort_ladder: str = ""
    intent_gate_last_rung: str = ""
    intent_gate_min_taxonomy_confidence: float = 0.0
    intent_gate_last_attempted_at: datetime | None = None
    intent_gate_last_success_at: datetime | None = None
    intent_gate_last_duration_ms: int | None = None
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
    total: int


class ChatSessionResponse(BaseModel):
    id: int
    title: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    tool_name: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionDetailResponse(ChatSessionResponse):
    messages: list[ChatMessageResponse] = Field(default_factory=list)


class ChatMessageRequest(BaseModel):
    text: str
    model: str | None = None
    attachment_ids: list[int] = Field(default_factory=list)


class ChatAttachmentResponse(BaseModel):
    id: int
    session_id: int
    message_id: int | None = None
    file_name: str
    mime_type: str
    byte_size: int
    extraction_error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ChatSessionRenameRequest(BaseModel):
    title: str


class ChatDeleteResponse(BaseModel):
    id: int
    deleted: bool


class ChatStatusResponse(BaseModel):
    enabled: bool
    ollama_running: bool
    ollama_last_error: str | None = None
    ollama_last_success_at: datetime | None = None
    chat_last_error: str | None = None
    mcp_status: str
    model: str
    available_models: list[str]


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
    recruiter_email_domain: str | None = None
    recruiter_phone: str | None = None
    recruiter_company: str | None = None
    employer_name: str | None = None
    employer_email: str | None = None
    employer_email_domain: str | None = None
    employer_phone: str | None = None
    employer_company: str | None = None
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
    opened_at: datetime | None = None
    open_count: int = 0
    reply_count: int = 0


class ConversationSummaryResponse(BaseModel):
    id: int
    root_recruiter_email_id: int
    recruiter: str
    recruiter_email: str | None = None
    subject: str
    status: str
    last_message_preview: str
    last_message_at: datetime
    unread_reply_count: int
    last_inbound_reply_at: datetime | None = None
    gmail_thread_link: str | None = None


class ConversationMessageResponse(BaseModel):
    id: int
    direction: str
    sender: str
    body: str
    snippet: str
    occurred_at: datetime
    read_at: datetime | None = None


class ConversationDetailResponse(ConversationSummaryResponse):
    to_email: str | None = None
    cc_email: str | None = None
    messages: list[ConversationMessageResponse] = Field(default_factory=list)


class ConversationReplyRequest(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


class EmailSearchHitResponse(BaseModel):
    section: str
    recruiter_email_id: int | None
    sender: str
    subject: str
    state: str
    detail: dict[str, object]
    occurred_at: datetime


class EmailSearchResponse(BaseModel):
    query: str
    hits: list[EmailSearchHitResponse]
    truncated: bool


class PremiumNumberResponse(BaseModel):
    id: int
    recruiter_email_id: int | None
    external_opportunity_id: int | None = None
    contact_id: int | None = None
    phone_number_display: str
    phone_number_normalized: str
    role: str = "recruiter"
    extraction_source: str = "ai"
    contact_email: str = ""
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
    source_url: str | None = None
    linkedin_url: str = ""
    source_section: str | None = None
    block_id: str | None = None
    evidence_offset_start: int | None = None
    evidence_offset_end: int | None = None
    colocation_verified: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PremiumNumberListResponse(BaseModel):
    items: list[PremiumNumberResponse]
    next_cursor: int | None
    has_next: bool


class UnknownNumberReviewCardResponse(BaseModel):
    id: int
    source_email_id: int | None
    source_external_opportunity_id: int | None = None
    source_lead_id: int | None = None
    target_contact_id: int | None = None
    secondary_contact_id: int | None = None
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
    contact_email: str = ""
    linkedin_url: str = ""
    contact_type: str = "unknown"
    recruiter_relevance_score: int = 0
    relevance_reason: str = ""
    extraction_source: str = "ai"
    scored_with: str = "legacy"
    gmail_open_url: str
    state: str
    role: str | None = None
    reason_code: str = "new_number"
    field_changes_json: str = ""
    occurrence_count: int = 1
    created_at: datetime
    updated_at: datetime
    evidence_at: datetime | None = None

    model_config = {"from_attributes": True}


class UnknownNumberReviewCardListResponse(BaseModel):
    items: list[UnknownNumberReviewCardResponse]
    next_cursor: int | None
    has_next: bool


class ExtractionAuditEntryResponse(BaseModel):
    id: int
    source_email_id: int | None
    source_external_opportunity_id: int | None
    raw_value: str
    normalized_value: str | None
    status: str
    stage: str
    reason: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ExtractionAuditListResponse(BaseModel):
    items: list[ExtractionAuditEntryResponse]


class RecruiterNumberResponse(BaseModel):
    id: int
    normalized_phone_number: str | None
    display_phone_number: str
    recruiter_name: str
    company: str
    secondary_company: str = ""
    designation: str
    recruiter_email: str
    recruiter_email_domain: str = ""
    employer_email_domain: str = ""
    is_favorite: bool = False
    emails: list[dict[str, object]] = Field(default_factory=list)
    phones: list[dict[str, object]] = Field(default_factory=list)
    first_detected_email_id: int | None
    source_type: str | None = None
    source_id: int | None = None
    source_link_url: str | None = None
    active_lead_id: int | None = None
    version_count: int = 0
    seen_count: int = 1
    linkedin_url: str = ""
    recruiter_verification_level: str = "unverified"
    do_not_work_again: bool = False
    do_not_work_again_reason: str = ""
    total_opportunity_count: int = 0
    last_email_received_at: datetime | None = None
    is_recruiter: bool = True
    is_employer: bool = False
    recruiter_relevance_score: int | None = None
    status: str = "Active"
    flagged: bool = False
    created_at: datetime
    updated_at: datetime


class EmployerNumberResponse(BaseModel):
    id: int
    normalized_phone_number: str | None
    display_phone_number: str
    owner_name: str
    designation: str = "Unknown"
    company: str
    secondary_company: str = ""
    employer_email: str = ""
    employer_email_domain: str = ""
    is_favorite: bool = False
    emails: list[dict[str, object]] = Field(default_factory=list)
    source_email_id: int | None
    source_type: str | None = None
    source_id: int | None = None
    source_link_url: str | None = None
    active_lead_id: int | None = None
    version_count: int = 0
    seen_count: int = 1
    phones: list[dict[str, object]] = Field(default_factory=list)
    linkedin_url: str = ""
    recruiter_verification_level: str = "unverified"
    do_not_work_again: bool = False
    do_not_work_again_reason: str = ""
    is_recruiter: bool = False
    is_employer: bool = True
    recruiter_relevance_score: int | None = None
    status: str = "Active"
    flagged: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PremiumNumberInventoryItemResponse(BaseModel):
    key: str
    kind: Literal["review", "contact"]
    id: int
    number: str
    owner: str
    company: str
    categories: list[Literal["Recruiter", "Employer"]]
    status: Literal["Pending", "Active", "Flagged", "Unscored"]
    score: int | None
    sourceType: Literal["gmail", "nvoids"] | None
    lastCheckedAt: datetime
    review: UnknownNumberReviewCardResponse | None = None
    recruiter: RecruiterNumberResponse | None = None
    employer: EmployerNumberResponse | None = None


class PremiumNumberInventoryListResponse(BaseModel):
    items: list[PremiumNumberInventoryItemResponse]
    next_cursor: int | None
    has_next: bool
    total: int


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
    email_id: int | None = None
    record_id: str | None = None
    email_subject: str
    email_sender: str
    gmail_open_url: str
    received_at: datetime | None
    job_title: str
    end_client: str
    location: str
    work_mode: str
    visa_restrictions: str
    resume_file_name: str = ""
    resume_asset_id: int | None = None
    implementation_partner: str = ""
    prime_vendor: str = ""
    domain: str = ""
    extracted_skills: str
    evidence: str
    recruiter_name: str = ""
    recruiter_email: str = ""
    recruiter_phone_display: str = ""
    recruiter_phone_normalized: str = ""
    recruiter_company: str = ""
    linkedin_url: str = ""
    status: str
    notes: str
    employment_type: str = ""
    rate_amount: float | None = None
    rate_currency: str = "USD"
    rate_unit: str = ""
    contract_duration: str = ""
    relocation_required: bool | None = None
    extension_likely: str = "unknown"
    end_client_confirmed: bool = False
    job_confidence: str = "unknown"
    cold_call_script: str | None = None
    cold_call_script_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RecruiterOpportunityListResponse(BaseModel):
    items: list[RecruiterOpportunityResponse]
    next_cursor: int | None
    has_next: bool
    total: int


class RecruiterOpportunityPatchRequest(BaseModel):
    status: str | None = None
    notes: str | None = None
    job_title: str | None = None
    location: str | None = None
    work_mode: str | None = None
    visa_restrictions: str | None = None
    resume_file_name: str | None = None
    implementation_partner: str | None = None
    prime_vendor: str | None = None
    end_client: str | None = None
    domain: str | None = None
    extracted_skills: str | None = None
    employment_type: str | None = None
    rate_amount: float | None = None
    rate_currency: str | None = None
    rate_unit: str | None = None
    contract_duration: str | None = None
    relocation_required: bool | None = None
    extension_likely: str | None = None
    end_client_confirmed: bool | None = None
    job_confidence: str | None = None


class ApplicationCreateRequest(BaseModel):
    resume_asset_id: int | None = Field(default=None, gt=0)
    recruiter_opportunity_id: int = Field(gt=0)
    dedupe_key: str = Field(min_length=1, max_length=64)


class ManualApplicationCreateRequest(BaseModel):
    resume_asset_id: int = Field(gt=0)
    recruiter_opportunity_id: int | None = Field(default=None, gt=0)
    dedupe_key: str = Field(min_length=1, max_length=64)
    manual_recruiter_name: str = Field(min_length=1, max_length=255)
    manual_recruiter_company: str = Field(min_length=1, max_length=255)
    manual_recruiter_email: str = Field(default="", max_length=255)
    manual_recruiter_phone: str = Field(default="", max_length=80)
    manual_recruiter_linkedin_url: str = Field(default="", max_length=2000)
    manual_job_title: str = Field(min_length=1)
    manual_end_client: str = Field(min_length=1)
    manual_jd_text: str = ""
    manual_source_note: str = ""
    submission_method: str = Field(default="email", min_length=1, max_length=20)
    resume_submitted_at: datetime | None = None
    location_snapshot: str = ""


class RejectionDetailTagInput(BaseModel):
    category: Literal[
        "missing_skill",
        "missing_experience",
        "missing_domain_knowledge",
        "rate_mismatch",
        "email_positioning",
        "other",
    ]
    value: str = ""


class RejectionDetailTagResponse(BaseModel):
    category: str
    value: str
    source: Literal["ai", "user"]
    confirmed_at: datetime | None = None


class ResumeSubmissionStatusUpdateRequest(BaseModel):
    new_status: Literal["viewed", "shortlisted", "offered", "hired", "rejected", "withdrawn"]
    rejection_detail_tags: list[RejectionDetailTagInput] = Field(default_factory=list)
    note: str = ""
    force: bool = False


class ApplicationPatchRequest(BaseModel):
    status: str | None = None
    next_action_type: str | None = Field(default=None, max_length=80)
    next_action_at: datetime | None = None
    closed_reason: str | None = Field(default=None, max_length=120)
    closed_reason_code: str | None = None


class ApplicationEventCreateRequest(BaseModel):
    event_type: Literal["note", "email_linked", "call_note"]
    note: str = ""
    linked_recruiter_email_id: int | None = Field(default=None, gt=0)


class ApplicationEventResponse(BaseModel):
    id: int
    owner_id: str
    application_id: int
    event_type: str
    event_source: str
    note: str
    linked_recruiter_email_id: int | None
    metadata_json: str
    occurred_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class ApplicationRTRRequest(BaseModel):
    role_scope: str = ""
    end_client_scope: str = ""
    expires_at: datetime | None = None


class ApplicationRTRUpdateRequest(BaseModel):
    status: Literal["confirmed", "expired", "revoked"]
    proof_attachment_id: int | None = Field(default=None, gt=0)
    proof_recruiter_email_id: int | None = Field(default=None, gt=0)


class ApplicationRTRResponse(BaseModel):
    id: int
    status: str
    role_scope: str
    end_client_scope: str
    requested_at: datetime
    confirmed_at: datetime | None
    expires_at: datetime | None
    proof_attachment_id: int | None
    proof_recruiter_email_id: int | None
    note: str

    model_config = {"from_attributes": True}


class ApplicationInterviewCreateRequest(BaseModel):
    round_type: Literal["recruiter_screen", "interview_1", "interview_2", "final_interview", "other"]
    scheduled_at: datetime | None = None
    format: str = ""
    interviewer_names: str = ""
    sync_application_status: bool = True


class ApplicationInterviewPatchRequest(BaseModel):
    scheduled_at: datetime | None = None
    format: str | None = None
    interviewer_names: str | None = None
    feedback: str | None = None
    result: Literal["scheduled", "completed", "passed", "failed", "cancelled", "rescheduled"] | None = None
    follow_up_task_note: str | None = None


class ApplicationInterviewResponse(BaseModel):
    id: int
    round_type: str
    scheduled_at: datetime | None
    format: str
    interviewer_names: str
    feedback: str
    result: str
    follow_up_task_note: str

    model_config = {"from_attributes": True}


class ApplicationSubmitToClientRequest(BaseModel):
    override_duplicate_warning: bool = False


class ApplicationDraftMessageRequest(BaseModel):
    message_kind: Literal["followup", "submission_to_recruiter"] = "followup"


class ApplicationDraftMessageResponse(BaseModel):
    to: str
    cc: str | None
    thread_id: str | None
    subject: str
    body: str
    source: str
    ai_model: str | None
    ai_error: str | None
    resume_context_status: str
    resume_file_name: str
    message_kind: str


class ApplicationSendMessageRequest(BaseModel):
    to: str
    cc: str | None = None
    subject: str
    body: str
    thread_id: str | None = None
    message_kind: Literal["followup", "submission_to_recruiter"] = "followup"
    include_resume: bool = True
    attachment_asset_ids: list[int] = Field(default_factory=list)
    draft_source: str = "unknown"
    ai_model: str | None = None

    @field_validator("to")
    @classmethod
    def validate_to(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Recipient email is required")
        return cleaned

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message body is required")
        return value


class ApplicationSkillGapResponse(BaseModel):
    source: str
    matched_required: list[str]
    missing_required: list[str]
    matched_preferred: list[str]
    missing_preferred: list[str]
    computed_at: datetime


class ApplicationResponse(BaseModel):
    id: int
    owner_id: str
    resume_asset_id: int
    resume_version_snapshot: int
    resume_file_name_snapshot: str
    resume_sha256_snapshot: str
    recruiter_opportunity_id: int | None
    recruiter_contact_id: int | None
    recruiter_name_snapshot: str
    recruiter_company_snapshot: str
    job_title_snapshot: str
    end_client_snapshot: str
    location_snapshot: str = ""
    resume_skills_snapshot: list[str] = Field(default_factory=list)
    record_id: str | None = None
    status: str
    status_changed_at: datetime
    resume_shared_at: datetime | None
    submitted_to_client_at: datetime | None
    next_action_type: str | None
    next_action_at: datetime | None
    follow_up_count: int
    last_contact_at: datetime | None
    closed_at: datetime | None
    closed_reason: str | None
    closed_reason_code: str | None
    resume_submission_status: str
    resume_submitted_at: datetime | None
    submission_method: str
    rejection_detail_tags: list[RejectionDetailTagResponse] = Field(default_factory=list)
    dedupe_key: str | None
    promoted_to_appts_application_id: int | None = None
    is_manual_entry: bool = False
    milestones_reached: dict[str, datetime] = Field(default_factory=dict)
    manual_recruiter_name: str = ""
    manual_recruiter_company: str = ""
    manual_recruiter_email: str = ""
    manual_recruiter_phone: str = ""
    manual_recruiter_linkedin_url: str = ""
    manual_job_title: str = ""
    manual_end_client: str = ""
    created_at: datetime
    updated_at: datetime
    current_recruiter_name: str = ""
    current_recruiter_company: str = ""
    current_recruiter_phone_display: str = ""
    current_recruiter_email: str = ""
    current_recruiter_linkedin_url: str = ""
    current_job_title: str = ""
    current_end_client: str = ""
    current_recruiter_categories: list[str] = Field(default_factory=list)
    current_recruiter_status: str = ""
    current_recruiter_verification_level: str = ""
    current_source_url: str | None = None
    source_recruiter_email_id: int | None = None
    ats_score: float | None = None
    ats_summary: str | None = None
    sent_gmail_message_link: str | None = None
    events: list[ApplicationEventResponse] = Field(default_factory=list)
    rtr_history: list[ApplicationRTRResponse] = Field(default_factory=list)
    interviews: list[ApplicationInterviewResponse] = Field(default_factory=list)
    skill_gap: ApplicationSkillGapResponse | None = None

    model_config = {"from_attributes": True}


class ApplicationSendMessageResponse(BaseModel):
    sent: bool
    gmail_message_id: str
    application: ApplicationResponse


class ApplicationListResponse(BaseModel):
    items: list[ApplicationResponse]
    next_cursor: int | None
    has_next: bool
    total: int


class RecordSourceResponse(BaseModel):
    type: str
    recruiter_email_id: int | None = None
    external_opportunity_id: int | None = None
    state: str | None = None
    subject: str = ""
    sender: str = ""


class RecordLineageResponse(BaseModel):
    lineage_id: str
    current_status: str
    closed_at: datetime | None = None
    event_count: int


class RecordEmailResponse(BaseModel):
    recruiter_email_id: int
    recipient_email: str | None = None
    cc_email: str | None = None
    sent_status: str
    sent_at: datetime | None = None


class RecordOutcomesResponse(BaseModel):
    sent: bool
    sent_count: int
    opened: bool
    open_count: int
    replied: bool
    inbound_reply_count: int
    first_reply_at: datetime | None = None
    days_to_first_reply: float | None = None
    interviewed: bool
    current_status: str | None = None


class RecordLifecycleEventResponse(BaseModel):
    id: int
    event_type: str
    occurred_at: datetime
    actor: str
    process_name: str
    related_record_type: str
    related_record_id: int | None = None
    note: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecordDetailResponse(BaseModel):
    record_id: str
    origin_type: str
    created_at: datetime
    source: RecordSourceResponse
    lineage: RecordLineageResponse | None = None
    recruiter_opportunity: RecruiterOpportunityResponse | None = None
    applications_enabled: bool
    resume_tracking_enabled: bool
    applications: list[ApplicationResponse] = Field(default_factory=list)
    legacy_applications: list[ApplicationResponse] = Field(default_factory=list)
    emails: list[RecordEmailResponse] = Field(default_factory=list)
    outcomes: RecordOutcomesResponse
    lifecycle_events: list[RecordLifecycleEventResponse] = Field(default_factory=list)


class ApplicationDashboardSummaryResponse(BaseModel):
    due_today: int
    waiting_on_recruiter: int
    interviews: int
    closed_recent: int
    pending_suggestions: int = 0


class OpportunityMatchResponse(BaseModel):
    opportunity: RecruiterOpportunityResponse
    score: float
    reasons: list[str]


class OpportunityMatchListResponse(BaseModel):
    items: list[OpportunityMatchResponse]


class RecruiterReputationResponse(BaseModel):
    recruiter_contact_id: int
    history_label: Literal["limited_history", "established"]
    outreach_count: int
    replies_count: int
    median_first_reply_business_days: float | None
    submissions_count: int
    interviews_after_submission_count: int
    offers_count: int
    last_active_at: datetime | None

    model_config = {"from_attributes": True}


class ApplicationSuggestionResponse(BaseModel):
    id: int
    application_id: int
    suggestion_type: Literal[
        "link_reply",
        "status_change",
        "next_action",
        "stale_prompt",
        "new_variant_needed",
        "email_positioning",
        "skill_gap_pattern",
    ]
    status: Literal["pending", "accepted", "dismissed"]
    confidence: Literal["high", "medium"]
    recruiter_email_id: int | None
    suggested_status: str | None
    suggested_next_action_type: str | None
    suggested_next_action_at: datetime | None
    reason: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class ApplicationSuggestionListResponse(BaseModel):
    items: list[ApplicationSuggestionResponse]


class ResumeFunnelMetricsResponse(BaseModel):
    resume_asset_id: int
    total_submissions: int
    not_submitted_count: int
    view_rate: float
    shortlist_rate: float
    interview_rate: float
    offer_rate: float
    hire_rate: float
    rejection_rate: float
    acceptance_rate: float
    median_days_to_shortlist: float | None
    median_days_to_interview: float | None
    median_days_to_offer: float | None
    top_rejection_reasons: list[dict[str, Any]]
    top_missing_skills: list[dict[str, Any]]


class ResumePerformanceSummaryItem(BaseModel):
    resume: ResumeResponse
    submission_count: int
    acceptance_rate: float


class ResumePerformanceSummaryResponse(BaseModel):
    items: list[ResumePerformanceSummaryItem]


class ApplicationOutreachMessageResponse(BaseModel):
    id: int
    application_id: int
    message_kind: str
    draft_source: str
    ai_model: str | None
    subject: str
    body: str
    sent_at: datetime


class ApplicationSuggestionResolveRequest(BaseModel):
    override_next_action_at: datetime | None = None


class BulkNumberReviewRequest(BaseModel):
    review_ids: list[int]


class BulkNumberReviewResultItem(BaseModel):
    review_id: int
    status: str


class BulkNumberReviewResponse(BaseModel):
    results: list[BulkNumberReviewResultItem]


class BulkContactActionRequest(BaseModel):
    contact_ids: list[int]


class BulkContactActionResultItem(BaseModel):
    contact_id: int
    status: str


class BulkContactActionResponse(BaseModel):
    results: list[BulkContactActionResultItem]


class ContactFieldChange(BaseModel):
    field: str
    label: str
    old: str
    new: str


class ContactRescoreResponse(BaseModel):
    id: int
    status: str
    changes: list[ContactFieldChange] = []


class ContactMergePreviewLead(BaseModel):
    id: int
    role: str
    company: str
    owner_name: str
    contact_email: str
    phone_number_display: str
    extraction_source: str
    created_at: datetime


class ContactMergePreviewSide(BaseModel):
    id: int
    recruiter_name: str
    owner_name: str
    company: str
    secondary_company: str = ""
    recruiter_email: str
    employer_email: str
    normalized_phone_number: str | None
    display_phone_number: str
    phones: list[dict] = Field(default_factory=list)
    emails: list[dict] = Field(default_factory=list)
    is_recruiter: bool
    is_employer: bool
    lead_count: int
    latest_evidence_at: datetime | None
    leads: list[ContactMergePreviewLead]


class ContactMergePreviewResponse(BaseModel):
    contact_a: ContactMergePreviewSide
    contact_b: ContactMergePreviewSide


class ContactMergeRequest(BaseModel):
    canonical_contact_id: int
    loser_contact_id: int


class ContactMergeResponse(BaseModel):
    canonical_contact_id: int
    loser_contact_id: int
    status: str


class DuplicateContactBackfillResponse(BaseModel):
    groups_merged: int
    contacts_merged: int


class RecentRunSkippedItemRetryRequest(BaseModel):
    skipped_item_ids: list[int]


class PendingNumberReviewCountResponse(BaseModel):
    count: int


class NumberReviewSubmitRequest(BaseModel):
    owner_name: str | None = None
    company: str | None = None
    display_phone_number: str | None = None
    contact_email: str | None = None
    designation: str | None = None
    linkedin_url: str | None = None


class ContactMergeApprovalRequest(BaseModel):
    canonical_contact_id: int | None = Field(default=None, gt=0)


class RecruiterNumberPatchRequest(BaseModel):
    recruiter_name: str | None = None
    company: str | None = None
    secondary_company: str | None = None
    designation: str | None = None
    recruiter_email: str | None = None
    phone_number: str | None = None
    phones: list[str] | None = None
    emails: list[str] | None = None
    linkedin_url: str | None = None
    recruiter_verification_level: Literal["unverified", "verified", "trusted"] | None = None
    do_not_work_again: bool | None = None
    do_not_work_again_reason: str | None = None
    is_favorite: bool | None = None


class EmployerNumberPatchRequest(BaseModel):
    owner_name: str | None = None
    designation: str | None = None
    is_favorite: bool | None = None
    company: str | None = None
    secondary_company: str | None = None
    employer_email: str | None = None
    phones: list[str] | None = None
    emails: list[str] | None = None
    linkedin_url: str | None = None
    recruiter_verification_level: Literal["unverified", "verified", "trusted"] | None = None
    do_not_work_again: bool | None = None
    do_not_work_again_reason: str | None = None


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
    queued_email_ids: list[int] = Field(default_factory=list)
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
    gate_error: str | None = None
    source_group_name: str | None = None
    source_group_email: str | None = None
    source_group_match_method: str | None = None
    source_group_trusted: bool | None = None
    qualification_result: str | None = None
    blocking_rule: str | None = None
    qualification_detail: str | None = None
    qualification_context: dict[str, object] | None = None
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
    source_count: int = 0
    requirement_count: int = 0
    multi_role_source_count: int = 0
    manifest_review_count: int = 0
    job_backend_id: str | None = None
    total_items: int | None = None
    processed_items: int = 0
    progress_pct: float | None = None
    queue_name: str | None = None
    sync_batch_id: str | None = None
    external_scrape_run_id: int | None = None
    created_at: datetime


class JobEnqueueResponse(BaseModel):
    run_key: str
    job_id: str
    status: str


class JobStatusResponse(RecentRunResponse):
    job_id: str | None = None


class JobQueueSummaryResponse(BaseModel):
    queued: int
    processing: int
    succeeded: int
    failed: int


class LiveReplyStatusResponse(BaseModel):
    count: int
    checked_at: datetime | None


class RecentRunListResponse(BaseModel):
    items: list[RecentRunResponse]
    next_cursor: int | None = None
    has_next: bool = False


class GmailRequirementGroupCreateRequest(BaseModel):
    value: str
    display_name: str | None = None
    enabled: bool = True


class GmailRequirementGroupBulkCreateRequest(BaseModel):
    values: str


class GmailRequirementGroupUpdateRequest(BaseModel):
    display_name: str | None = None
    enabled: bool | None = None


class GmailRequirementGroupResponse(BaseModel):
    id: int
    owner_id: str
    display_name: str
    group_email: str
    normalized_group_email: str
    group_slug: str | None = None
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


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


SettingsBootstrapResponse.model_rebuild()


class ProductivityEventCreateRequest(BaseModel):
    event_type: str
    event_source: str = "ui"
    entity_id: int | None = None
    entity_type: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProductivityEventResponse(BaseModel):
    id: int
    owner_id: str
    event_type: str
    event_source: str
    entity_id: int | None
    entity_type: str
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


# --- v3 relationship intelligence -------------------------------------------


class RelationshipLabelRequest(BaseModel):
    left_opportunity_id: int
    right_opportunity_id: int
    verdict: str
    reason: str = ""
    labeler: str = ""
    sampler: str = ""


class RelationshipJudgmentRequest(BaseModel):
    verdict: str
    note: str = ""
    correct_member_ids: list[int] = Field(default_factory=list)


class EntityAliasMergeRequest(BaseModel):
    entity_type: str
    keep_id: int
    alias_id: int
