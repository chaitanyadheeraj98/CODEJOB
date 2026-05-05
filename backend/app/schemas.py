from datetime import datetime
import json
from typing import Any

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


class SettingsRequest(BaseModel):
    enabled: bool = True
    gmail_query: str = "is:unread in:inbox recruiter"
    mail_date: str | None = None
    min_salary: int | None = None
    accepted_locations: list[str] = Field(default_factory=list)
    visa_required_allowed: bool = False
    remote_preference: str = "any"
    role_keywords: list[str] = Field(default_factory=list)
    must_have_skills: list[str] = Field(default_factory=list)
    free_text_guidance: str = ""
    qualification_threshold: float = 0.6
    feature_auto_polling: bool = False
    feature_auto_send: bool = False
    feature_retry_queue: bool = False

    @field_validator("mail_date")
    @classmethod
    def validate_mail_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        datetime.strptime(value, "%Y-%m-%d")
        return value


class SettingsResponse(SettingsRequest):
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
    def parse_routing_json(cls, value: Any) -> Any:
        if value in (None, ""):
            return []
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []
        return value


class GmailStatusResponse(BaseModel):
    configured: bool
    authenticated: bool
    token_path: str
    last_sync_at: datetime | None
    detail: str


class GmailSyncResponse(BaseModel):
    sync_batch_id: str
    imported_count: int
    skipped_count: int
    error_count: int


class CandidateListResponse(BaseModel):
    items: list[EmailResponse]
    next_cursor: int | None
    has_next: bool


class AutomationRunResponse(BaseModel):
    status: str
    detail: str
    email_id: int | None = None


class AutomationRunRequest(BaseModel):
    mail_date: str | None = None

    @field_validator("mail_date")
    @classmethod
    def validate_mail_date(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        datetime.strptime(value, "%Y-%m-%d")
        return value
