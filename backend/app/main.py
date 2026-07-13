import hashlib
import json
import logging
import re
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Mapping, TypedDict, TypeVar, cast
import threading
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.ai.draft_formatting import normalize_draft_text_size
from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_MISSING,
    RESUME_CONTEXT_RULES_ONLY,
)
from app.ai.resume_context import extract_resume_context
from app.cold_call import ColdCallContext, find_allowed_cold_call_skills, generate_cold_call_script
from app.automation import (
    RunOrchestrator,
    RunOrchestratorDependencies,
    RunOrchestratorRequest,
)
from app.db import Base, SessionLocal, engine, ensure_sqlite_phase0_columns
from app.gates import classify_email_intent
from app.ai.groq_client import groq_request_mode_for_model
from app.gmail_client import (
    GmailMessageCandidate,
    get_message_rfc_message_id,
    gmail_auth_status,
    is_gmail_configured,
    list_unread_candidates_by_query,
    mark_message_processed,
    append_tracking_sheet_row,
    oauth_bootstrap_status,
    oauth_authorization_url,
    send_reply_with_attachment,
    send_new_email_with_attachment,
    start_oauth_bootstrap,
)
from app.gmail_labeling import GmailLabelingService, LabelRuleInput
from app.external_feeds.service import ExternalFeedService
from app.external_feeds.models import ExternalOpportunity, ExternalScrapeRun
from app.models import (
    AttachmentAsset,
    CustomSkillTaxonomyEntry,
    DraftEditFeedback,
    EmployerNumber,
    GmailRequirementGroup,
    JobIntentTaxonomyEntry,
    NumberReviewQueue,
    PremiumNumberLead,
    ProductivityEvent,
    RecentRun,
    RecentRunSkippedItem,
    RecruiterEmail,
    RecruiterNumber,
    RecruiterOpportunity,
    ResumeAsset,
    SyncRun,
    UserSettings,
)
from app.models import RecipientRoutingFeedback
from app.parsing import build_skills_json_payload
from app.parsing.skill_audit import is_suspicious_skill_blob
from app.telegram_bot import TelegramBotService, TelegramReply
from app.phase0 import (
    DEFAULT_FALLBACK_DRAFT_TEMPLATE,
    DEFAULT_SIGNATURE_EMAIL,
    DEFAULT_SIGNATURE_NAME,
    DEFAULT_SIGNATURE_PHONE,
    RoutingResult,
    draft_reply,
    email_domain,
    extract_email_address,
    greeting_from_to_contact,
    hard_filter_check,
    is_recruiter_like,
    normalize_employer_domains,
    parse_email,
    parse_email_with_details,
    should_block_f2f,
)
from app.routing import RoutingDecision
from app.recent_runs import build_gmail_message_url, row_to_recent_run_dict
from app.skill_taxonomy import (
    clear_skill_taxonomy_cache,
    extract_skills_text,
    load_skill_taxonomy,
    normalize_skill_token,
    normalize_skills_text,
    normalize_taxonomy_text,
)
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES
from app.premium_numbers.phone_normalization import best_display_phone, canonicalize_phone
from app.query_bucket import sanitize_saved_queries
from app.runtime_state import runtime_state
from app.job_intent_learning import normalize_job_intent_phrase
from app.services import analytics_service, policy_service
from app.services.auto_runner_service import AutoRunnerService
from app.services.candidate_runtime_service import CandidateRuntimeDeps, CandidateRuntimeService, resolve_resume_display_name
from app.services.gmail_group_source_service import (
    canonical_group_display_name,
    normalize_google_group_email,
    normalize_google_group_slug,
    parse_group_inputs,
)
from app.services.gmail_labeling_runtime_service import GmailLabelingRuntimeService
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService
from app.services.routing_runtime_service import RoutingRuntimeDeps, RoutingRuntimeService
from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService
from app.services.settings_bootstrap_service import SettingsBootstrapService
from app.services.startup_service import StartupService
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.schemas import (
    AIStatusResponse,
    ApproveJobIntentSignalRequest,
    ApproveSendRequest,
    ApproveSkillRequest,
    AttachmentAssetResponse,
    AttachmentAssetUpdateRequest,
    AutomationRunRequest,
    AutomationRunResponse,
    BulkApproveJobIntentSignalsResponse,
    BulkApproveSkillsResponse,
    BulkRejectRequest,
    CandidateListResponse,
    CustomSkillTaxonomyEntryResponse,
    DismissJobIntentSignalRequest,
    DismissSkillRequest,
    EmailResponse,
    GmailStatusResponse,
    GmailSyncResponse,
    GmailLabelingPreviewRequest,
    GmailLabelingPreviewResponse,
    IngestEmailRequest,
    JobIntentTaxonomyEntryResponse,
    OAuthStartResponse,
    OAuthUrlResponse,
    EmployerNumberResponse,
    EmployerNumberListResponse,
    PremiumNumberListResponse,
    PremiumNumberResponse,
    PendingSkillResponse,
    RecruiterNumberResponse,
    RecruiterNumberListResponse,
    RecruiterOpportunityDeleteResponse,
    RecruiterOpportunityListResponse,
    RecruiterOpportunityPatchRequest,
    RecruiterOpportunityResponse,
    RejectRequest,
    ResolveRecipientsRequest,
    ResumeResponse,
    ResumeUpdateRequest,
    SentItemDetailsResponse,
    SettingsBootstrapResponse,
    SettingsRequest,
    SettingsResponse,
    UnknownNumberReviewCardListResponse,
    UnknownNumberReviewCardResponse,
    ProductivityEventCreateRequest,
    ProductivityEventResponse,
    ProductivityBarPoint,
    ProductivityTrendResponse,
    RecentRunItemListResponse,
    RecentRunItemResponse,
    RecentRunListResponse,
    RecentRunResponse,
    RegenerateCandidateRequest,
    TelegramStatusResponse,
    ExternalFeedSyncResponse,
    ExternalScrapeRunResponse,
    GmailRequirementGroupBulkCreateRequest,
    GmailRequirementGroupCreateRequest,
    GmailRequirementGroupResponse,
    GmailRequirementGroupUpdateRequest,
)
from app.semantic.embeddings_service import (
    begin_embedding_latency_capture,
    embedding_to_json,
    end_embedding_latency_capture,
    generate_embedding,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global auto_runner_thread, telegram_service, gmail_labeling_service
    StartupService(
        ensure_default_settings=_ensure_default_settings,
        ensure_labeling_service=gmail_labeling_runtime_service.ensure_service,
        init_telegram_service=_init_telegram_service,
        auto_runner_loop=_auto_runner_loop,
    ).startup()
    auto_runner_thread = runtime_state.auto_runner_thread
    telegram_service = runtime_state.telegram_service
    gmail_labeling_service = runtime_state.gmail_labeling_service
    yield
    StartupService(
        ensure_default_settings=_ensure_default_settings,
        ensure_labeling_service=gmail_labeling_runtime_service.ensure_service,
        init_telegram_service=_init_telegram_service,
        auto_runner_loop=_auto_runner_loop,
    ).shutdown()
    auto_runner_thread = runtime_state.auto_runner_thread
    telegram_service = runtime_state.telegram_service
    gmail_labeling_service = runtime_state.gmail_labeling_service


app = FastAPI(title=settings.app_name, lifespan=lifespan)
logger = logging.getLogger(__name__)
last_gmail_sync_at: datetime | None = None
ai_running: bool = False
ai_last_error: str | None = None
ai_last_started_at: datetime | None = None
ai_last_finished_at: datetime | None = None
ai_last_duration_ms: int | None = None
embedding_last_error: str | None = None
embedding_last_attempted_at: datetime | None = None
embedding_last_success_at: datetime | None = None
embedding_last_duration_ms: int | None = None
groq_last_error: str | None = None
groq_last_attempted_at: datetime | None = None
groq_last_success_at: datetime | None = None
groq_last_duration_ms: int | None = None
groq_last_provider_result: str | None = None
semantic_input_source: str | None = None
semantic_input_chars: int | None = None
semantic_chunks: int | None = None
semantic_fallback_reason: str | None = None
keyword_source: str | None = None
thread_snapshot_used: bool | None = None
thread_snapshot_email_id: int | None = None
telegram_service: TelegramBotService | None = runtime_state.telegram_service
telegram_runtime: TelegramRuntime | None = None
orchestration_service: OrchestrationService | None = None
auto_runner_service: AutoRunnerService | None = None
routing_runtime_service: RoutingRuntimeService | None = None
candidate_runtime_service: CandidateRuntimeService | None = None
scoring_runtime_service: ScoringRuntimeService | None = None
settings_bootstrap_service = SettingsBootstrapService(session_factory=SessionLocal)
gmail_labeling_runtime_service = GmailLabelingRuntimeService()
telegram_action_lock = runtime_state.telegram_action_lock
telegram_pending_inputs = runtime_state.telegram_pending_inputs
auto_runner_thread: threading.Thread | None = runtime_state.auto_runner_thread
auto_runner_stop_event = runtime_state.auto_runner_stop_event
gmail_labeling_service: GmailLabelingService | None = runtime_state.gmail_labeling_service
external_feed_service = ExternalFeedService()


class PolicyQuery(TypedDict):
    force_unread: bool
    include_labels: list[str]
    exclude_labels: list[str]
    date_mode: str


class PolicyRun(TypedDict):
    run_mode: str
    batch_limit: int
    dry_run: bool


class PolicyQualification(TypedDict):
    location_strictness: str
    score_threshold_override_enabled: bool
    score_threshold_override_value: float


class PolicyConfig(TypedDict):
    version: int
    query: PolicyQuery
    run: PolicyRun
    qualification: PolicyQualification
ai_last_draft_source: str | None = None


def _set_last_gmail_sync_at(ts: datetime) -> None:
    global last_gmail_sync_at
    last_gmail_sync_at = ts


def _set_ai_runtime(values: dict[str, object]) -> None:
    global ai_running, ai_last_error, ai_last_draft_source, ai_last_started_at, ai_last_finished_at, ai_last_duration_ms
    global semantic_input_source, semantic_input_chars, semantic_chunks, semantic_fallback_reason
    global keyword_source, thread_snapshot_used, thread_snapshot_email_id
    if "ai_running" in values:
        ai_running = bool(values["ai_running"])
    if "ai_last_error" in values:
        ai_last_error = cast(str | None, values["ai_last_error"])
    if "ai_last_draft_source" in values:
        ai_last_draft_source = cast(str | None, values["ai_last_draft_source"])
    if "ai_last_started_at" in values:
        ai_last_started_at = cast(datetime | None, values["ai_last_started_at"])
    if "ai_last_finished_at" in values:
        ai_last_finished_at = cast(datetime | None, values["ai_last_finished_at"])
    if "ai_last_duration_ms" in values:
        ai_last_duration_ms = cast(int | None, values["ai_last_duration_ms"])
    if "semantic_input_source" in values:
        semantic_input_source = cast(str | None, values["semantic_input_source"])
    if "semantic_input_chars" in values:
        semantic_input_chars = cast(int | None, values["semantic_input_chars"])
    if "semantic_chunks" in values:
        semantic_chunks = cast(int | None, values["semantic_chunks"])
    if "semantic_fallback_reason" in values:
        semantic_fallback_reason = cast(str | None, values["semantic_fallback_reason"])
    if "keyword_source" in values:
        keyword_source = cast(str | None, values["keyword_source"])
    if "thread_snapshot_used" in values:
        thread_snapshot_used = cast(bool | None, values["thread_snapshot_used"])
    if "thread_snapshot_email_id" in values:
        thread_snapshot_email_id = cast(int | None, values["thread_snapshot_email_id"])


def _read_saved_gmail_queries(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return sanitize_saved_queries(parsed)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

EVENT_WEIGHTS: dict[str, float] = {
    "approved_sent": 4.0,
    "needs_review_marked": 2.0,
    "recent_run_recorded": 1.0,
    "failed_mapping_marked": -3.0,
    "view_needs_review": 0.2,
    "view_failed_mapping": 0.1,
    "view_recent_runs": 0.2,
    "view_sent_items": 0.2,
    "view_run_queue": 0.1,
    "view_premium_numbers": 0.1,
}

ALLOWED_VIEW_EVENTS = {
    "view_needs_review",
    "view_failed_mapping",
    "view_recent_runs",
    "view_sent_items",
    "view_run_queue",
    "view_premium_numbers",
}

RANGE_OPTIONS = {"last_1h", "current_day", "current_week", "current_month", "current_year", "last_5y"}
BUSINESS_TZ = ZoneInfo("America/Chicago")
BUCKET_OPTIONS = {"five_min", "hour", "day", "month", "quarter"}


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _record_productivity_event(
    db: Session,
    *,
    event_type: str,
    event_source: str,
    entity_id: int | None = None,
    metadata: Mapping[str, object] | None = None,
    occurred_at: datetime | None = None,
) -> ProductivityEvent:
    return analytics_service.record_productivity_event(
        db,
        owner_id=settings.owner_id,
        event_weights=EVENT_WEIGHTS,
        event_type=event_type,
        event_source=event_source,
        entity_id=entity_id,
        metadata=metadata,
        occurred_at=occurred_at,
    )


def _event_response(event: ProductivityEvent) -> ProductivityEventResponse:
    return analytics_service.event_response(event)


def _range_bounds(range_key: str) -> tuple[datetime, datetime]:
    return analytics_service.range_bounds(range_key, BUSINESS_TZ)


def _ensure_utc(ts: datetime) -> datetime:
    return analytics_service.ensure_utc(ts)


def _generate_embedding_with_health(text: str) -> tuple[list[float], str]:
    global embedding_last_error, embedding_last_attempted_at, embedding_last_success_at, embedding_last_duration_ms
    embedding_last_attempted_at = datetime.now(UTC)
    started_at = embedding_last_attempted_at
    try:
        vector, provider = generate_embedding(text)
    except Exception as exc:
        finished_at = datetime.now(UTC)
        embedding_last_duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        embedding_last_error = str(exc)
        raise
    finished_at = datetime.now(UTC)
    embedding_last_duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    embedding_last_success_at = finished_at
    embedding_last_error = None
    return vector, provider


def _default_bucket_for_range(range_key: str) -> str:
    if range_key == "last_1h":
        return "five_min"
    if range_key == "current_day":
        return "hour"
    if range_key == "current_week":
        return "day"
    if range_key == "current_month":
        return "day"
    if range_key == "current_year":
        return "month"
    return "quarter"


def _bucket_start(ts: datetime, bucket: str) -> datetime:
    if bucket == "five_min":
        minute = (ts.minute // 5) * 5
        return ts.replace(minute=minute, second=0, microsecond=0)
    if bucket == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "month":
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month = ts.month
    quarter_month = ((month - 1) // 3) * 3 + 1
    return ts.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _add_months(ts: datetime, months: int) -> datetime:
    month_index = (ts.month - 1) + months
    year = ts.year + month_index // 12
    month = month_index % 12 + 1
    return datetime(year, month, 1, tzinfo=UTC)


def _next_bucket(ts: datetime, bucket: str) -> datetime:
    if bucket == "five_min":
        return ts + timedelta(minutes=5)
    if bucket == "hour":
        return ts + timedelta(hours=1)
    if bucket == "day":
        return ts + timedelta(days=1)
    if bucket == "month":
        return _add_months(ts, 1)
    return _add_months(ts, 3)


def _mail_date_utc_window(selected: date) -> tuple[datetime, datetime]:
    start_local = datetime(selected.year, selected.month, selected.day, tzinfo=BUSINESS_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _mail_date_filter_field(states: list[str]) -> str:
    normalized = {s.strip().lower() for s in states if s.strip()}
    if normalized == {"approved_sent"}:
        return "sent_at"
    return "gmail_received_at"


def _is_approved_sent_only(states: list[str]) -> bool:
    normalized = {s.strip().lower() for s in states if s.strip()}
    return normalized == {"approved_sent"}


def _source_label(source: str | None) -> str:
    normalized = (source or "").strip().lower()
    if normalized == "gmail":
        return "Gmail"
    if normalized == "nvoids":
        return "Nvoids"
    if normalized == "manual":
        return "Manual"
    return normalized or "Unknown"


def _clean_optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.lower() == "unknown":
        return None
    return text


def _json_object(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return cast(dict[str, object], parsed) if isinstance(parsed, dict) else {}


def _json_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    items: list[str] = []
    for item in parsed:
        text = _clean_optional_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _record_string_value(record: Mapping[str, object] | None, key: str) -> str | None:
    if not record:
        return None
    return _clean_optional_text(record.get(key))


def _record_string_list(record: Mapping[str, object] | None, key: str) -> list[str]:
    if not record:
        return []
    value = record.get(key)
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _clean_optional_text(item)
        if text and text not in items:
            items.append(text)
    return items


def _unique_strings(*groups: list[str]) -> list[str]:
    result: list[str] = []
    for group in groups:
        for item in group:
            text = _clean_optional_text(item)
            if text and text not in result:
                result.append(text)
    return result


def _parse_sender_contact(sender: str) -> tuple[str | None, str | None]:
    name, email_address = parseaddr(sender or "")
    clean_email = _clean_optional_text(email_address)
    clean_name = _clean_optional_text(name)
    return clean_name, clean_email


def _extract_external_post_id(external_message_id: str | None) -> str | None:
    message_id = (external_message_id or "").strip()
    match = re.match(r"^nvoids:(?:nvoids:)?(.+)$", message_id, re.IGNORECASE)
    if not match:
        return None
    return _clean_optional_text(match.group(1))


def _extract_labeled_value(text: str, *labels: str) -> str | None:
    if not text.strip():
        return None
    for label in labels:
        pattern = rf"(?im)^\s*{re.escape(label)}\s*[:\-]\s*(.+?)\s*$"
        match = re.search(pattern, text)
        if match:
            return _clean_optional_text(match.group(1))
    return None


def _extract_experience_required(text: str) -> str | None:
    labeled = _extract_labeled_value(text, "experience", "experience required", "required experience")
    if labeled:
        return labeled
    match = re.search(r"(?i)\b(\d{1,2}\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience)\b", text)
    if match:
        return _clean_optional_text(match.group(1))
    return None


def _load_external_opportunity_for_sent_details(db: Session, email: RecruiterEmail) -> ExternalOpportunity | None:
    external_post_id = _extract_external_post_id(email.external_message_id)
    if not external_post_id:
        return None
    return (
        db.query(ExternalOpportunity)
        .filter(
            ExternalOpportunity.owner_id == settings.owner_id,
            ExternalOpportunity.external_post_id == external_post_id,
        )
        .first()
    )


def _load_recruiter_opportunity_for_sent_details(db: Session, email: RecruiterEmail) -> RecruiterOpportunity | None:
    row = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.source_email_id == email.id,
        )
        .first()
    )
    if row:
        return row
    if not email.external_message_id:
        return None
    return (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.gmail_message_id == email.external_message_id,
        )
        .first()
    )


def _load_recruiter_number_for_sent_details(db: Session, email: RecruiterEmail, recruiter_email: str | None) -> RecruiterNumber | None:
    row = (
        db.query(RecruiterNumber)
        .filter(
            RecruiterNumber.owner_id == settings.owner_id,
            RecruiterNumber.first_detected_email_id == email.id,
        )
        .first()
    )
    if row:
        return row
    if not recruiter_email:
        return None
    return (
        db.query(RecruiterNumber)
        .filter(
            RecruiterNumber.owner_id == settings.owner_id,
            RecruiterNumber.recruiter_email == recruiter_email,
        )
        .order_by(RecruiterNumber.updated_at.desc(), RecruiterNumber.id.desc())
        .first()
    )


def _load_premium_lead_for_sent_details(db: Session, email: RecruiterEmail) -> PremiumNumberLead | None:
    return (
        db.query(PremiumNumberLead)
        .filter(
            PremiumNumberLead.owner_id == settings.owner_id,
            PremiumNumberLead.recruiter_email_id == email.id,
        )
        .order_by(
            PremiumNumberLead.is_recruiter_relevant.desc(),
            PremiumNumberLead.recruiter_relevance_score.desc(),
            PremiumNumberLead.id.desc(),
        )
        .first()
    )


def _sent_item_requirement_link(email: RecruiterEmail, external: ExternalOpportunity | None) -> str | None:
    if email.source == "nvoids":
        return _clean_optional_text((external.source_url if external else None) or email.external_thread_id)
    return email.gmail_message_url


def _sent_item_mandatory_skills(
    parser_details: Mapping[str, object] | None,
    ats_breakdown: Mapping[str, object] | None,
) -> list[str]:
    matched_raw = _record_string_list(ats_breakdown, "matched_raw_skills")
    missing_raw = _record_string_list(ats_breakdown, "missing_raw_skills")
    if matched_raw or missing_raw:
        return _unique_strings(matched_raw, missing_raw)
    skills_audit = parser_details.get("skills_audit") if parser_details else None
    if isinstance(skills_audit, Mapping):
        return _unique_strings(_record_string_list(skills_audit, "known"), _record_string_list(skills_audit, "unknown"))
    return []


def _sent_item_missing_skills(
    parser_details: Mapping[str, object] | None,
    ats_breakdown: Mapping[str, object] | None,
) -> list[str]:
    missing_raw = _record_string_list(ats_breakdown, "missing_raw_skills")
    if missing_raw:
        return missing_raw
    skills_audit = parser_details.get("skills_audit") if parser_details else None
    if isinstance(skills_audit, Mapping):
        unknown = _record_string_list(skills_audit, "unknown")
        if unknown:
            return unknown
    unknown_skills = parser_details.get("unknown_skills") if parser_details else None
    if isinstance(unknown_skills, list):
        return _unique_strings([str(item) for item in unknown_skills])
    return []


def _build_sent_item_details(db: Session, email: RecruiterEmail) -> SentItemDetailsResponse:
    parser_details = _json_object(email.parser_details_json)
    ats_breakdown = _json_object(email.ats_breakdown_json)
    body_text = email.body or ""
    sender_name, sender_email = _parse_sender_contact(email.sender)
    external = _load_external_opportunity_for_sent_details(db, email) if email.source == "nvoids" else None
    recruiter_opportunity = _load_recruiter_opportunity_for_sent_details(db, email)
    premium_lead = _load_premium_lead_for_sent_details(db, email)
    recruiter_email = (
        _clean_optional_text(external.recruiter_email if external else None)
        or _clean_optional_text(email.recipient_email)
        or sender_email
    )
    recruiter_number = _load_recruiter_number_for_sent_details(db, email, recruiter_email)
    company = (
        _clean_optional_text(external.company if external else None)
        or _clean_optional_text(recruiter_opportunity.client if recruiter_opportunity else None)
        or _clean_optional_text(recruiter_number.company if recruiter_number else None)
        or _clean_optional_text(premium_lead.company if premium_lead else None)
    )
    return SentItemDetailsResponse(
        email_id=email.id,
        source_type=email.source,
        source_label=_source_label(email.source),
        requirement_received_link=_sent_item_requirement_link(email, external),
        sent_gmail_message_link=email.gmail_sent_message_url,
        resume_variant_sent=_clean_optional_text(email.resume_file_name),
        attached_files=_json_string_list(email.sent_attachment_file_names_json),
        company=company,
        recruiter_name=(
            _clean_optional_text(external.recruiter_name if external else None)
            or _clean_optional_text(recruiter_number.recruiter_name if recruiter_number else None)
            or _clean_optional_text(premium_lead.owner_name if premium_lead else None)
            or sender_name
        ),
        recruiter_email=recruiter_email,
        recruiter_phone=(
            _clean_optional_text(external.recruiter_phone if external else None)
            or _clean_optional_text(recruiter_number.display_phone_number if recruiter_number else None)
            or _clean_optional_text(premium_lead.phone_number_display if premium_lead else None)
        ),
        end_client=(
            _extract_labeled_value(body_text, "end client", "end-client")
            or _extract_labeled_value(body_text, "client")
        ),
        implementation_partner=_extract_labeled_value(body_text, "implementation partner", "implementor"),
        vendor=_extract_labeled_value(body_text, "vendor"),
        domain_mentioned=(
            _extract_labeled_value(body_text, "domain", "domain mentioned")
            or _extract_labeled_value(body_text, "industry")
        ),
        experience_required=_extract_experience_required(body_text),
        mandatory_skills=_sent_item_mandatory_skills(parser_details, ats_breakdown),
        missing_skills=_sent_item_missing_skills(parser_details, ats_breakdown),
        ats_score=email.ats_score,
        ats_summary=_clean_optional_text(email.ats_summary),
        to_email=_clean_optional_text(email.recipient_email),
        cc_email=_clean_optional_text(email.cc_email),
        sent_at=email.sent_at,
    )


def _build_telegram_digest(prefix: str, result: AutomationRunResponse) -> str:
    return (
        f"{prefix}\n"
        f"Status: {result.status}\n"
        f"Detail: {result.detail}\n"
        f"Query: {result.effective_query or '-'}\n"
        f"Matched: {result.matched_count if result.matched_count is not None else '-'} | "
        f"Queued: {result.queued_count if result.queued_count is not None else '-'} | "
        f"Skipped: {result.skipped_count if result.skipped_count is not None else '-'} | "
        f"Failed: {result.failed_count if result.failed_count is not None else '-'}"
    )


def _tg_btn(text: str, data: str) -> dict[str, str]:
    return TelegramRuntime._tg_btn(text, data)


def _telegram_paginate_buttons(
    buttons: list[dict[str, str]],
    page: int,
    *,
    menu_action: str,
    include_home: bool = True,
    include_back: bool = False,
) -> list[list[dict[str, str]]]:
    return TelegramRuntime._paginate_buttons(
        buttons,
        page,
        menu_action=menu_action,
        include_home=include_home,
        include_back=include_back,
    )


def _compose_gmail_query(base_query: str, mail_date: str | None = None, policy: PolicyConfig | None = None) -> str:
    tokens = [token for token in (base_query or "").split() if token.lower() != "is:unread"]
    normalized_base = " ".join(tokens).strip()
    return policy_service.compose_gmail_query(normalized_base, mail_date, policy)


def _format_query_preflight(user_settings: UserSettings, policy: PolicyConfig) -> str:
    resolved = policy_service.effective_run_inputs(
        gmail_query=user_settings.gmail_query,
        default_gmail_query=user_settings.default_gmail_query,
        default_date_mode=user_settings.default_date_mode,
        policy_json=user_settings.policy_json,
        saved_mail_date=user_settings.mail_date,
    )
    date_mode = resolved.policy["query"]["date_mode"]
    effective_query = resolved.effective_query
    return (
        "Run preflight:\n"
        f"Saved query: {user_settings.gmail_query}\n"
        f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
        f"Default date mode: {user_settings.default_date_mode or 'today'}\n"
        f"Date mode: {date_mode}\n"
        f"Saved date: {user_settings.mail_date or 'any'}\n"
        f"Effective query: {effective_query}"
    )


def _poll_interval_minutes(user_settings: UserSettings) -> int:
    return max(1, min(int(user_settings.feature_auto_poll_interval_minutes or 10), 1440))


def _nvoids_poll_interval_minutes(user_settings: UserSettings) -> int:
    return max(1, min(int(user_settings.feature_nvoids_poll_interval_minutes or 30), 1440))


def _nvoids_batch_limit(user_settings: UserSettings) -> int:
    return max(1, min(int(user_settings.nvoids_batch_limit or 10), 50))


def _maybe_generate_cold_call_script(email: object, *, resume: ResumeAsset | None, user_settings: UserSettings | object) -> None:
    _ = user_settings
    if not bool(getattr(email, "is_premium", False)):
        setattr(email, "cold_call_script", "")
        setattr(email, "cold_call_script_source", None)
        setattr(email, "cold_call_script_error", None)
        return
    if not resume:
        setattr(email, "cold_call_script", "")
        setattr(email, "cold_call_script_source", None)
        setattr(email, "cold_call_script_error", "No active resume uploaded for cold call script generation.")
        return


def _get_auto_runner_service() -> AutoRunnerService:
    global auto_runner_service
    if auto_runner_service is None:
        auto_runner_service = AutoRunnerService(
            session_factory=SessionLocal,
            get_settings=_get_settings,
            run_once=automation_run_once,
            run_nvoids_once=lambda db, max_items: external_feed_service.sync_nvoids(
                db,
                owner_id=settings.owner_id,
                max_items=max_items,
            ),
            action_lock=telegram_action_lock,
            stop_event=auto_runner_stop_event,
        )
    return auto_runner_service


def _auto_runner_loop() -> None:
    _get_auto_runner_service().run_loop()


def _handle_telegram_command(chat_id: int, user_id: str, username: str, text: str) -> str | TelegramReply:
    if not telegram_runtime:
        if text.strip().lower() == "/start":
            return TelegramRuntime._main_menu_reply()
        pending_mode = telegram_pending_inputs.get(chat_id)
        if pending_mode:
            if text.strip().lower() in {"/cancel", "cancel"}:
                telegram_pending_inputs.pop(chat_id, None)
                return TelegramReply(text="Cancelled pending action.", inline_keyboard=[[TelegramRuntime._tg_btn("Home", "menu:main:0")]])
            return TelegramReply(text=f"Pending input mode: {pending_mode}")
        return "Telegram runtime unavailable."
    return telegram_runtime.handle_command(chat_id, user_id, username, text)


def _handle_telegram_callback(
    chat_id: int,
    user_id: str,
    username: str,
    callback_data: str,
    message_id: int,
) -> str | TelegramReply:
    if not telegram_runtime:
        if callback_data.startswith("menu:"):
            action, page = TelegramRuntime._parse_callback_data(callback_data)
            reply = TelegramRuntime._menu_reply(action, page)
            reply.edit_message_id = message_id
            return reply
        if callback_data.startswith("flow:"):
            mode = callback_data.split(":", 1)[1].strip()
            if mode:
                telegram_pending_inputs[chat_id] = mode
            return TelegramReply(
                text=TelegramRuntime._pending_prompt(mode),
                edit_message_id=message_id,
                inline_keyboard=[[TelegramRuntime._tg_btn("Cancel", "cancel:pending")], [TelegramRuntime._tg_btn("Home", "menu:main:0")]],
            )
        if callback_data.startswith("cancel:pending"):
            telegram_pending_inputs.pop(chat_id, None)
            return TelegramReply(text="Cancelled pending action.", edit_message_id=message_id, inline_keyboard=[[TelegramRuntime._tg_btn("Home", "menu:main:0")]])
        return "Telegram runtime unavailable."
    return telegram_runtime.handle_callback(chat_id, user_id, username, callback_data, message_id)


def _init_telegram_service() -> TelegramBotService | None:
    global telegram_runtime
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return None
    allowed_chat_ids = TelegramRuntime.parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)
    if not allowed_chat_ids:
        logger.warning("Telegram bot token exists but TELEGRAM_ALLOWED_CHAT_IDS is empty. Bot will not start.")
        return None
    telegram_runtime = TelegramRuntime(
        TelegramRuntimeDeps(
            session_factory=SessionLocal,
            get_settings=_get_settings,
            read_policy_from_settings=lambda user_settings: policy_service.read_policy_from_settings(user_settings.policy_json),
            policy_dry_run=policy_service.policy_dry_run,
            format_query_preflight=_format_query_preflight,
            poll_interval_minutes=_poll_interval_minutes,
            build_telegram_digest=_build_telegram_digest,
            gmail_auth_status=gmail_auth_status,
            ai_status=ai_status,
            gmail_sync=gmail_sync,
            automation_run_once=automation_run_once,
            get_candidate_review=_get_candidate_review,
            approve_and_send=approve_and_send,
            reject_candidate=reject_candidate,
            owner_id=settings.owner_id,
            action_lock=telegram_action_lock,
            action_pin=lambda: (settings.telegram_action_pin or "").strip(),
            auth_ttl_minutes=lambda: max(1, int(settings.telegram_auth_ttl_minutes or 30)),
        )
    )
    service = TelegramBotService(
        token=token,
        allowed_chat_ids=allowed_chat_ids,
        alerts_enabled=settings.telegram_alerts_enabled,
        command_handler=telegram_runtime.handle_command,
        callback_handler=telegram_runtime.handle_callback,
    )
    service.start()
    logger.info("Telegram bot started with %s authorized chat(s)", len(allowed_chat_ids))
    return service


def _get_routing_runtime_service() -> RoutingRuntimeService:
    global routing_runtime_service
    if routing_runtime_service is None:
        routing_runtime_service = RoutingRuntimeService(
            RoutingRuntimeDeps(
                owner_id=settings.owner_id,
                get_employer_domains=lambda db: _csv_to_list(_get_settings(db).employer_domains),
            )
        )
    return routing_runtime_service


def _get_candidate_runtime_service() -> CandidateRuntimeService:
    global candidate_runtime_service
    if candidate_runtime_service is None:
        candidate_runtime_service = CandidateRuntimeService(
            CandidateRuntimeDeps(
                get_settings=_get_settings,
                evaluate_routing_policy=lambda db, sender, subject, body, snippet="", routing_confirmed=False, precomputed=None: _get_routing_runtime_service().evaluate_routing_policy(
                    db, sender, subject, body, snippet, routing_confirmed, precomputed=precomputed
                ),
                apply_routing_decision=lambda email, routing: _get_routing_runtime_service().apply_routing_decision(email, routing),
            )
        )
    return candidate_runtime_service


def _get_scoring_runtime_service() -> ScoringRuntimeService:
    global scoring_runtime_service
    if scoring_runtime_service is None:
        scoring_runtime_service = ScoringRuntimeService(
            ScoringRuntimeDeps(generate_embedding_with_health=_generate_embedding_with_health)
        )
    return scoring_runtime_service


def _get_orchestration_service() -> OrchestrationService:
    global orchestration_service
    if orchestration_service is None:
        orchestration_service = OrchestrationService(
            OrchestrationDeps(
                owner_id=settings.owner_id,
                model_name=settings.deepseek_model_fast,
                get_settings=_get_settings,
                active_resume=_active_resume,
                enabled_resumes=_enabled_resumes,
                enabled_attachment_assets=_enabled_attachment_assets,
                effective_run_inputs=lambda user_settings, requested_mail_date: policy_service.effective_run_inputs(
                    gmail_query=user_settings.gmail_query,
                    default_gmail_query=user_settings.default_gmail_query,
                    default_date_mode=user_settings.default_date_mode,
                    policy_json=user_settings.policy_json,
                    saved_mail_date=user_settings.mail_date,
                    requested_mail_date=requested_mail_date,
                ),
                compute_blended_ai_score=lambda **kwargs: _compute_blended_ai_score(**kwargs),
                select_best_resume_match=lambda **kwargs: _select_best_resume_match(**kwargs),
                analyze_email_routing=lambda db, sender, subject, body, snippet: _analyze_email_routing(db, sender, subject, body, snippet),
                build_user_fallback_draft=lambda db, user_settings, sender, role, parsed, greeting_line, resume_file_name: _build_user_fallback_draft(
                    db,
                    user_settings,
                    sender=sender,
                    role=role,
                    parsed=parsed,
                    greeting_line=greeting_line,
                    resume_file_name=resume_file_name,
                ),
                apply_routing_result=lambda email, routing: _apply_routing_result(email, routing),
                apply_gmail_label_for_email=_apply_gmail_label_for_email,
                log_gmail_labeling_stats=_log_gmail_labeling_stats,
                build_run_response=_build_run_response,
                record_productivity_event=_record_productivity_event,
                policy_threshold=lambda user_settings, policy: policy_service.policy_threshold(user_settings.qualification_threshold, policy),
                policy_batch_limit=policy_service.policy_batch_limit,
                policy_dry_run=policy_service.policy_dry_run,
                policy_f2f_block=_policy_f2f_block,
                evaluate_routing_policy=_evaluate_routing_policy,
                apply_routing_decision=_apply_routing_decision,
                capture_premium_numbers=_capture_premium_numbers,
                percentile_ms=_percentile_ms,
                begin_embedding_latency_capture=begin_embedding_latency_capture,
                end_embedding_latency_capture=end_embedding_latency_capture,
                embedding_latency_log_enabled=lambda: settings.semantic_embedding_latency_log_enabled,
                embedding_provider=lambda: settings.effective_semantic_embedding_provider,
                embedding_model=lambda: settings.effective_semantic_embedding_model,
                evaluate_routing_for_email=_evaluate_routing_for_email,
                is_terminal_state=_is_terminal_state,
                email_domain=_email_domain,
                telegram_notify=lambda msg: telegram_service.notify(msg) if telegram_service else None,
                build_telegram_digest=_build_telegram_digest,
                set_last_gmail_sync_at=lambda ts: _set_last_gmail_sync_at(ts),
                set_ai_runtime=lambda vals: _set_ai_runtime(vals),
                is_gmail_configured=lambda: is_gmail_configured(),
                gmail_auth_status=lambda: gmail_auth_status(),
                oauth_bootstrap_status=lambda: oauth_bootstrap_status(),
                list_unread_candidates_by_query=lambda *args, **kwargs: list_unread_candidates_by_query(*args, **kwargs),
                is_recruiter_like=lambda sender, subject, body: is_recruiter_like(sender, subject, body),
                classify_email_intent=lambda **kwargs: classify_email_intent(**kwargs),
                parse_email=lambda subject, body: parse_email(subject, body),
                parse_email_with_details=lambda subject, body, **kwargs: parse_email_with_details(subject, body, **kwargs),
                hard_filter_check=lambda parsed, user_settings, effective_policy: hard_filter_check(parsed, user_settings, effective_policy),
                greeting_from_to_contact=lambda to_email, body: greeting_from_to_contact(to_email, body),
                generate_reply_with_ai_or_fallback=lambda **kwargs: generate_reply_with_ai_or_fallback(**kwargs),
                send_reply_with_attachment=lambda *args, **kwargs: send_reply_with_attachment(*args, **kwargs),
                send_new_email_with_attachment=lambda *args, **kwargs: send_new_email_with_attachment(*args, **kwargs),
                mark_message_processed=lambda message_id: mark_message_processed(message_id),
                append_tracking_sheet_row=lambda **kwargs: append_tracking_sheet_row(**kwargs),
            )
        )
    return orchestration_service


def _ensure_default_settings() -> None:
    settings_bootstrap_service.ensure_default_settings()


def _get_settings(db: Session) -> UserSettings:
    return settings_bootstrap_service.get_settings(db)


def _to_csv(values: list[str]) -> str:
    return ",".join(v.strip() for v in values if v.strip())


def _csv_to_list(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _policy_f2f_block(parsed: dict[str, str | int | bool], policy: PolicyConfig) -> tuple[bool, str]:
    normalized = policy_service.normalize_policy(policy)
    qualification = normalized["qualification"]
    strictness = policy_service.as_str(qualification.get("location_strictness", "balanced"), "balanced")
    f2f_blocked, f2f_reason = should_block_f2f(parsed)
    if strictness == "lenient":
        f2f_blocked = False
        f2f_reason = ""
    blocked, reason = policy_service.should_block_non_texas_f2f(
        parsed,
        normalized,
        f2f_blocked=f2f_blocked,
        f2f_reason=f2f_reason,
    )
    if blocked:
        return True, reason
    return policy_service.should_block_unknown_location_under_strict(parsed, normalized)

def _active_resume(db: Session) -> ResumeAsset | None:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .order_by(ResumeAsset.version.desc())
        .first()
    )


def _list_resumes(db: Session) -> list[ResumeAsset]:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id)
        .order_by(ResumeAsset.version.desc(), ResumeAsset.updated_at.desc())
        .all()
    )


def _enabled_resumes(db: Session) -> list[ResumeAsset]:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_enabled.is_(True))
        .order_by(ResumeAsset.is_current.desc(), ResumeAsset.updated_at.desc(), ResumeAsset.version.desc(), ResumeAsset.id.desc())
        .all()
    )


def _most_recent_enabled_resume(db: Session, *, exclude_resume_id: int | None = None) -> ResumeAsset | None:
    query = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_enabled.is_(True))
    )
    if exclude_resume_id is not None:
        query = query.filter(ResumeAsset.id != exclude_resume_id)
    return query.order_by(ResumeAsset.updated_at.desc(), ResumeAsset.version.desc(), ResumeAsset.id.desc()).first()


def _set_legacy_current_resume(
    db: Session,
    *,
    target_resume: ResumeAsset | None,
) -> None:
    current_items = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .all()
    )
    target_id = target_resume.id if target_resume else None
    for item in current_items:
        if item.id != target_id:
            item.is_current = False
    if target_resume:
        target_resume.is_current = True


def _normalize_resume_skills_text(raw: str | None) -> str:
    return normalize_skills_text(raw, preserve_unknown=True)


def _resume_skills_text_for_cold_call(resume: ResumeAsset | None, resume_text: str) -> str:
    stored = str(getattr(resume, "skills_text", "") or "").strip()
    if stored and stored.lower() != "none_detected":
        return stored
    derived = extract_skills_text(resume_text)
    return "" if derived == "none_detected" else derived


def _refresh_resume_embedding(resume: ResumeAsset) -> None:
    try:
        resume_text = _semantic_text_for_resume(resume)
        if resume_text.strip():
            resume_vector, _provider = _generate_embedding_with_health(resume_text)
            resume.semantic_embedding = embedding_to_json(resume_vector)
        else:
            resume.semantic_embedding = None
    except Exception as exc:
        logger.warning("Resume semantic embedding skipped: %s", exc)


def _resume_for_candidate(db: Session, email: RecruiterEmail) -> ResumeAsset | None:
    if email.resume_asset_id:
        pinned = (
            db.query(ResumeAsset)
            .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == email.resume_asset_id)
            .first()
        )
        if pinned:
            return pinned
    return _active_resume(db)


def _select_best_resume_match(
    *,
    subject: str,
    body: str,
    parsed: dict[str, str | int],
    parser_details: dict[str, object] | None = None,
    user_settings: UserSettings,
    email_row: RecruiterEmail | None,
    resumes: list[ResumeAsset] | None = None,
    fallback_resume: ResumeAsset | None = None,
    db: Session,
    owner_id: str | None,
    external_thread_id: str | None,
) -> object:
    return _get_scoring_runtime_service().select_best_resume_match(
        subject=subject,
        body=body,
        parsed=parsed,
        parser_details=parser_details,
        user_settings=user_settings,
        email_row=email_row,
        resumes=resumes if resumes is not None else _enabled_resumes(db),
        fallback_resume=fallback_resume if fallback_resume is not None else _active_resume(db),
        db=db,
        owner_id=owner_id,
        external_thread_id=external_thread_id,
    )


def _list_attachment_assets(db: Session) -> list[AttachmentAsset]:
    return (
        db.query(AttachmentAsset)
        .filter(AttachmentAsset.owner_id == settings.owner_id)
        .order_by(AttachmentAsset.created_at.desc(), AttachmentAsset.id.desc())
        .all()
    )


def _enabled_attachment_assets(db: Session) -> list[AttachmentAsset]:
    return (
        db.query(AttachmentAsset)
        .filter(AttachmentAsset.owner_id == settings.owner_id, AttachmentAsset.is_enabled.is_(True))
        .order_by(AttachmentAsset.created_at.asc(), AttachmentAsset.id.asc())
        .all()
    )


def _enabled_attachment_file_names(db: Session) -> list[str]:
    return [item.file_name for item in _enabled_attachment_assets(db)]


def _clean_custom_skill_name(value: str | None) -> str:
    return " ".join(str(value or "").strip().split())


def _canonicalize_custom_skill_name(value: str | None) -> str:
    cleaned = _clean_custom_skill_name(value)
    if not cleaned:
        return ""
    return normalize_skill_token(cleaned, preserve_unknown=False) or cleaned


def _normalize_custom_skill_aliases(aliases: list[str] | None, *, canonical_name: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = {normalize_taxonomy_text(canonical_name)}
    for item in aliases or []:
        alias = _clean_custom_skill_name(item)
        if not alias:
            continue
        normalized = normalize_taxonomy_text(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(alias)
    return ordered


def _serialize_custom_skill_entry(entry: CustomSkillTaxonomyEntry) -> CustomSkillTaxonomyEntryResponse:
    payload = CustomSkillTaxonomyEntryResponse.model_validate(entry).model_dump()
    try:
        aliases = json.loads(entry.aliases_json or "[]")
    except json.JSONDecodeError:
        aliases = []
    payload["aliases"] = [str(item).strip() for item in aliases if str(item).strip()]
    return CustomSkillTaxonomyEntryResponse.model_validate(payload)


def _list_approved_custom_skill_entries(db: Session) -> list[CustomSkillTaxonomyEntry]:
    return (
        db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == settings.owner_id,
            CustomSkillTaxonomyEntry.status == "approved",
        )
        .order_by(CustomSkillTaxonomyEntry.canonical_name.asc(), CustomSkillTaxonomyEntry.id.asc())
        .all()
    )


def _known_or_suppressed_pending_skill_keys(db: Session) -> set[str]:
    suppressed = {
        normalize_taxonomy_text(row.canonical_name)
        for row in db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == settings.owner_id,
            CustomSkillTaxonomyEntry.status.in_(("approved", "dismissed")),
        )
        .all()
        if normalize_taxonomy_text(row.canonical_name)
    }
    suppressed.update(load_skill_taxonomy().exact_lookup.keys())
    return suppressed


def _list_pending_unknown_skills(db: Session) -> list[PendingSkillResponse]:
    suppressed = _known_or_suppressed_pending_skill_keys(db)
    aggregated: dict[str, dict[str, object]] = {}
    rows = (
        db.query(RecruiterEmail.id, RecruiterEmail.skills_json, RecruiterEmail.parser_details_json)
        .filter(
            RecruiterEmail.owner_id == settings.owner_id,
            or_(
                RecruiterEmail.skills_json.is_not(None),
                RecruiterEmail.parser_details_json.is_not(None),
            ),
        )
        .order_by(RecruiterEmail.id.desc())
        .all()
    )
    for email_id, skills_json, parser_details_json in rows:
        unknown_skills: list[object] = []
        if skills_json:
            try:
                skills_payload = json.loads(skills_json)
            except json.JSONDecodeError:
                skills_payload = None
            if isinstance(skills_payload, dict):
                raw_unknown = skills_payload.get("unknown", [])
                if isinstance(raw_unknown, list):
                    unknown_skills = raw_unknown
        if not unknown_skills and parser_details_json:
            try:
                payload = json.loads(parser_details_json)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                raw_unknown = payload.get("unknown_skills", [])
                if isinstance(raw_unknown, list):
                    unknown_skills = raw_unknown
        if not unknown_skills:
            continue
        seen_for_candidate: set[str] = set()
        for item in unknown_skills:
            skill_name = _clean_custom_skill_name(str(item))
            normalized = normalize_taxonomy_text(skill_name)
            if (
                not skill_name
                or not normalized
                or is_suspicious_skill_blob(skill_name)
                or normalized in suppressed
                or normalized in seen_for_candidate
            ):
                continue
            seen_for_candidate.add(normalized)
            bucket = aggregated.setdefault(
                normalized,
                {
                    "skill_name": skill_name,
                    "normalized_name": normalized,
                    "occurrence_count": 0,
                    "candidate_ids": [],
                },
            )
            bucket["occurrence_count"] = int(bucket["occurrence_count"]) + 1
            candidate_ids = cast(list[int], bucket["candidate_ids"])
            candidate_ids.append(int(email_id))
    results: list[PendingSkillResponse] = []
    for item in aggregated.values():
        candidate_ids = sorted(set(cast(list[int], item["candidate_ids"])), reverse=True)
        results.append(
            PendingSkillResponse(
                skill_name=str(item["skill_name"]),
                normalized_name=str(item["normalized_name"]),
                occurrence_count=int(item["occurrence_count"]),
                candidate_ids=candidate_ids,
            )
        )
    results.sort(key=lambda item: (-item.occurrence_count, item.skill_name.lower(), item.normalized_name))
    return results


def _upsert_custom_skill_entry(
    db: Session,
    *,
    skill_name: str,
    canonical_name: str | None = None,
    aliases: list[str] | None = None,
    category: str = "custom",
    cluster_hint: str | None = None,
    status: str,
    auto_commit: bool = True,
) -> CustomSkillTaxonomyEntry:
    effective_canonical_name = _canonicalize_custom_skill_name(canonical_name or skill_name)
    if not effective_canonical_name:
        raise HTTPException(status_code=400, detail="Skill name required")
    normalized_target = normalize_taxonomy_text(effective_canonical_name)
    normalized_category = normalize_taxonomy_text(category) or "custom"
    normalized_aliases = _normalize_custom_skill_aliases(aliases, canonical_name=effective_canonical_name)
    existing = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(CustomSkillTaxonomyEntry.owner_id == settings.owner_id)
        .order_by(CustomSkillTaxonomyEntry.id.asc())
        .all()
    )
    for row in existing:
        if normalize_taxonomy_text(row.canonical_name) != normalized_target:
            continue
        row.canonical_name = effective_canonical_name
        row.aliases_json = json.dumps(normalized_aliases, separators=(",", ":"))
        row.category = normalized_category
        row.cluster_hint = _clean_custom_skill_name(cluster_hint) or None
        row.status = status
        if auto_commit:
            clear_skill_taxonomy_cache()
            db.commit()
            db.refresh(row)
        return row
    created = CustomSkillTaxonomyEntry(
        owner_id=settings.owner_id,
        canonical_name=effective_canonical_name,
        aliases_json=json.dumps(normalized_aliases, separators=(",", ":")),
        category=normalized_category,
        cluster_hint=_clean_custom_skill_name(cluster_hint) or None,
        status=status,
    )
    db.add(created)
    if auto_commit:
        clear_skill_taxonomy_cache()
        db.commit()
        db.refresh(created)
    return created


def _serialize_job_intent_entry(entry: JobIntentTaxonomyEntry) -> JobIntentTaxonomyEntryResponse:
    payload = JobIntentTaxonomyEntryResponse.model_validate(entry).model_dump()
    try:
        sample_evidence = json.loads(entry.sample_evidence_json or "[]")
    except json.JSONDecodeError:
        sample_evidence = []
    payload["sample_evidence"] = [str(item).strip() for item in sample_evidence if str(item).strip()]
    return JobIntentTaxonomyEntryResponse.model_validate(payload)


def _list_job_intent_entries(db: Session, *, status: str) -> list[JobIntentTaxonomyEntry]:
    return (
        db.query(JobIntentTaxonomyEntry)
        .filter(
            JobIntentTaxonomyEntry.owner_id == settings.owner_id,
            JobIntentTaxonomyEntry.status == status,
        )
        .order_by(
            JobIntentTaxonomyEntry.source_examples_count.desc(),
            JobIntentTaxonomyEntry.confidence_aggregate.desc(),
            JobIntentTaxonomyEntry.phrase.asc(),
            JobIntentTaxonomyEntry.id.asc(),
        )
        .all()
    )


def _upsert_job_intent_entry(
    db: Session,
    *,
    phrase: str,
    polarity: str,
    status: str,
    auto_commit: bool = True,
) -> JobIntentTaxonomyEntry:
    cleaned_phrase = str(phrase or "").strip()
    normalized_phrase = normalize_job_intent_phrase(cleaned_phrase)
    cleaned_polarity = str(polarity or "").strip().lower()
    if not normalized_phrase:
        raise HTTPException(status_code=400, detail="Intent-learning phrase required")
    if not cleaned_polarity:
        raise HTTPException(status_code=400, detail="Intent-learning polarity required")
    existing = (
        db.query(JobIntentTaxonomyEntry)
        .filter(
            JobIntentTaxonomyEntry.owner_id == settings.owner_id,
            JobIntentTaxonomyEntry.normalized_phrase == normalized_phrase,
            JobIntentTaxonomyEntry.polarity == cleaned_polarity,
        )
        .order_by(JobIntentTaxonomyEntry.id.asc())
        .first()
    )
    if existing:
        existing.phrase = cleaned_phrase or existing.phrase
        existing.status = status
        if auto_commit:
            db.commit()
            db.refresh(existing)
        else:
            db.flush()
        return existing
    created = JobIntentTaxonomyEntry(
        owner_id=settings.owner_id,
        phrase=cleaned_phrase,
        normalized_phrase=normalized_phrase,
        polarity=cleaned_polarity,
        source_examples_count=0,
        sample_evidence_json="[]",
        confidence_aggregate=0.0,
        last_intent_type=None,
        status=status,
    )
    db.add(created)
    if auto_commit:
        db.commit()
        db.refresh(created)
    else:
        db.flush()
    return created


def _semantic_text_for_email(subject: str, body: str, role: str, skills_text: str) -> str:
    return _get_scoring_runtime_service().semantic_text_for_email(subject, body, role, skills_text)


def _semantic_text_for_resume(resume: ResumeAsset | None) -> str:
    return _get_scoring_runtime_service().semantic_text_for_resume(resume)


def _ensure_embedding_cached(current_payload: str | None, text: str) -> tuple[list[float], str | None, str]:
    return _get_scoring_runtime_service().ensure_embedding_cached(current_payload, text)


def _compute_blended_ai_score(
    *,
    subject: str,
    body: str,
    parsed: dict[str, str | int],
    user_settings: UserSettings,
    email_row: RecruiterEmail | None,
    resume: ResumeAsset | None,
    db: Session | None = None,
    owner_id: str | None = None,
    external_thread_id: str | None = None,
) -> tuple[float, str, str, str | None, str | None, object]:
    return _get_scoring_runtime_service().compute_blended_ai_score(
        subject=subject,
        body=body,
        parsed=parsed,
        user_settings=user_settings,
        email_row=email_row,
        resume=resume,
        db=db,
        owner_id=owner_id,
        external_thread_id=external_thread_id,
    )


def _percentile_ms(values: list[float], percentile: float) -> float:
    return _get_scoring_runtime_service().percentile_ms(values, percentile)


def _is_terminal_state(email: RecruiterEmail) -> bool:
    return email.state in {"approved_sent", "rejected", "auto_rejected"}


def _email_domain(address: str) -> str:
    return email_domain(address)


def _learned_recipient_pairs(db: Session, sender: str) -> list[tuple[str, str]]:
    return _get_routing_runtime_service().learned_recipient_pairs(db, sender)


def _apply_routing_result(email: RecruiterEmail, routing: RoutingResult) -> None:
    _get_routing_runtime_service().apply_routing_result(email, routing)


def _apply_routing_decision(email: RecruiterEmail, routing: RoutingDecision) -> None:
    _get_routing_runtime_service().apply_routing_decision(email, routing)


def _capture_premium_numbers(db: Session, email: RecruiterEmail) -> None:
    _get_candidate_runtime_service().capture_premium_numbers(db, email)


def _routing_is_sendable(email: RecruiterEmail) -> bool:
    return _get_routing_runtime_service().routing_is_sendable(email)


def _evaluate_routing_for_email(email: RecruiterEmail) -> RoutingDecision:
    return _get_routing_runtime_service().evaluate_routing_for_email(email)


def _analyze_email_routing(db: Session, sender: str, subject: str, body: str, snippet: str = "") -> RoutingResult:
    return _get_routing_runtime_service().analyze_email_routing(db, sender, subject, body, snippet)


def _evaluate_routing_policy(
    db: Session | None,
    sender: str,
    subject: str,
    body: str,
    snippet: str = "",
    routing_confirmed: bool = False,
    *,
    precomputed: RoutingResult | None = None,
) -> RoutingDecision:
    return _get_routing_runtime_service().evaluate_routing_policy(
        db,
        sender,
        subject,
        body,
        snippet,
        routing_confirmed,
        precomputed=precomputed,
    )


def _apply_draft_learning(db: Session, draft: str) -> str:
    return _get_candidate_runtime_service().apply_draft_learning(db, draft)


def _build_user_fallback_draft(
    db: Session,
    user_settings: UserSettings,
    *,
    sender: str,
    role: str,
    parsed: dict[str, str | int | bool],
    greeting_line: str,
    resume_file_name: str | None,
) -> str:
    return _get_candidate_runtime_service().build_user_fallback_draft(
        db,
        user_settings,
        sender=sender,
        role=role,
        parsed=parsed,
        greeting_line=greeting_line,
        resume_file_name=resume_file_name,
    )


def _fill_missing_gmail_rfc_ids(db: Session, emails: list[RecruiterEmail]) -> None:
    if not is_gmail_configured() or not Path(settings.google_token_path).exists():
        return

    changed = False
    for email in emails:
        if email.source != "gmail" or email.external_rfc_message_id or not email.external_message_id:
            continue
        try:
            rfc_message_id = get_message_rfc_message_id(email.external_message_id)
        except Exception:
            continue
        if rfc_message_id:
            email.external_rfc_message_id = rfc_message_id
            changed = True
    if changed:
        db.commit()


def _build_run_response(
    status: str,
    detail: str,
    email: RecruiterEmail | None = None,
    *,
    run_key: str | None = None,
    effective_query: str | None = None,
    matched_count: int | None = None,
    queued_count: int | None = None,
    skipped_count: int | None = None,
    failed_count: int | None = None,
    auto_sent_count: int | None = None,
    auto_send_failed_count: int | None = None,
    retry_promoted_count: int | None = None,
    retry_skipped_count: int | None = None,
) -> AutomationRunResponse:
    if not email:
        return AutomationRunResponse(
            status=status,
            detail=detail,
            run_key=run_key,
            effective_query=effective_query,
            matched_count=matched_count,
            queued_count=queued_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            auto_sent_count=auto_sent_count,
            auto_send_failed_count=auto_send_failed_count,
            retry_promoted_count=retry_promoted_count,
            retry_skipped_count=retry_skipped_count,
        )
    return AutomationRunResponse(
        status=status,
        detail=detail,
        run_key=run_key,
        email_id=email.id,
        gmail_message_url=email.gmail_message_url,
        decision_reason=email.decision_reason,
        skip_reason=email.skip_reason,
        routing_reason=email.routing_reason,
        applied_gmail_label=email.applied_gmail_label,
        applied_gmail_label_id=email.applied_gmail_label_id,
        effective_query=effective_query,
        matched_count=matched_count,
        queued_count=queued_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        auto_sent_count=auto_sent_count,
        auto_send_failed_count=auto_send_failed_count,
        retry_promoted_count=retry_promoted_count,
        retry_skipped_count=retry_skipped_count,
    )


def _recent_run_response(row: RecentRun) -> RecentRunResponse:
    return RecentRunResponse.model_validate(row_to_recent_run_dict(row))


def _recent_run_item_response(row: RecentRunSkippedItem) -> RecentRunItemResponse:
    gmail_message_url = row.gmail_message_url
    if row.source_type == "gmail" and not gmail_message_url:
        gmail_message_url = build_gmail_message_url(
            external_message_id=row.external_message_id,
            external_thread_id=row.external_thread_id,
        )
    return RecentRunItemResponse(
        id=row.id,
        run_key=row.run_key,
        run_source=row.run_source,
        source_type=row.source_type,
        outcome=row.outcome,
        reason_code=row.reason_code,
        reason_detail=row.reason_detail,
        external_message_id=row.external_message_id,
        external_thread_id=row.external_thread_id,
        candidate_email_id=row.candidate_email_id,
        external_opportunity_id=row.external_opportunity_id,
        title_or_subject=row.title_or_subject,
        sender=row.sender,
        location=row.location,
        source_url=row.source_url,
        gmail_message_url=gmail_message_url,
        intent_type=row.intent_type,
        intent_confidence=row.intent_confidence,
        intent_reason=row.intent_reason,
        intent_evidence=_json_string_list(row.intent_evidence_json),
        intent_negative_evidence=_json_string_list(row.intent_negative_evidence_json),
        gate_action=row.gate_action,
        gate_provider=row.gate_provider,
        source_group_name=row.source_group_name,
        source_group_email=row.source_group_email,
        source_group_match_method=row.source_group_match_method,
        source_group_trusted=row.source_group_trusted,
        qualification_result=row.qualification_result,
        blocking_rule=row.blocking_rule,
        qualification_detail=row.qualification_detail,
        qualification_context=_json_object(row.qualification_context_json),
        created_at=row.created_at,
    )


def _settings_response_from_model(s: UserSettings) -> SettingsResponse:
    policy = policy_service.read_policy_from_settings(s.policy_json)
    return SettingsResponse(
        enabled=s.enabled,
        gmail_query=s.gmail_query,
        default_gmail_query=(s.default_gmail_query or "").strip() or (s.gmail_query or "").strip() or "is:unread in:inbox recruiter",
        saved_gmail_queries=_read_saved_gmail_queries(s.saved_gmail_queries_json),
        mail_date=s.mail_date,
        default_date_mode=policy_service.normalize_default_date_mode(s.default_date_mode),
        min_salary=s.min_salary,
        accepted_locations=[v for v in s.accepted_locations.split(",") if v],
        visa_required_allowed=s.visa_required_allowed,
        remote_preference=s.remote_preference,
        role_keywords=[v for v in s.role_keywords.split(",") if v],
        must_have_skills=[v for v in s.must_have_skills.split(",") if v],
        employer_domains=sorted(normalize_employer_domains(_csv_to_list(s.employer_domains))),
        free_text_guidance=s.free_text_guidance,
        qualification_threshold=s.qualification_threshold,
        feature_auto_polling=s.feature_auto_polling,
        feature_auto_poll_interval_minutes=_poll_interval_minutes(s),
        feature_nvoids_enabled=s.feature_nvoids_enabled,
        feature_nvoids_auto_sync=s.feature_nvoids_auto_sync,
        feature_nvoids_poll_interval_minutes=_nvoids_poll_interval_minutes(s),
        nvoids_batch_limit=_nvoids_batch_limit(s),
        nvoids_detail_title_mode=(s.nvoids_detail_title_mode or "job_details").strip().lower() or "job_details",
        nvoids_locations=_csv_to_list(s.nvoids_locations),
        feature_auto_send=s.feature_auto_send,
        feature_retry_queue=s.feature_retry_queue,
        feature_ai_enabled=s.feature_ai_enabled,
        feature_ai_extractor_enabled=s.feature_ai_extractor_enabled,
        feature_semantic_enabled=s.feature_semantic_enabled,
        feature_groq_job_parser_enabled=s.feature_groq_job_parser_enabled,
        feature_gmail_requirement_groups_enabled=s.feature_gmail_requirement_groups_enabled,
        draft_text_size=normalize_draft_text_size(s.draft_text_size),
        fallback_draft_template=s.fallback_draft_template or DEFAULT_FALLBACK_DRAFT_TEMPLATE,
        signature_name=(s.signature_name or "").strip() or DEFAULT_SIGNATURE_NAME,
        signature_phone=(s.signature_phone or "").strip() or DEFAULT_SIGNATURE_PHONE,
        signature_email=(s.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL,
        preferred_employer_cc_email=(s.preferred_employer_cc_email or "").strip().lower(),
        resume_display_name=(s.resume_display_name or "").strip(),
        policy=policy,
        policy_profile_options=list(policy_service.policy_profiles().keys()),
        policy_profile_selected=policy_service.selected_policy_profile(policy),
        owner_id=s.owner_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _json_object(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _list_gmail_requirement_groups(db: Session) -> list[GmailRequirementGroup]:
    return (
        db.query(GmailRequirementGroup)
        .filter(GmailRequirementGroup.owner_id == settings.owner_id)
        .order_by(GmailRequirementGroup.display_name.asc(), GmailRequirementGroup.id.asc())
        .all()
    )


def _gmail_requirement_group_response(row: GmailRequirementGroup) -> GmailRequirementGroupResponse:
    return GmailRequirementGroupResponse.model_validate(row)


def _create_gmail_requirement_group(
    db: Session,
    *,
    value: str,
    display_name: str | None = None,
    enabled: bool = True,
) -> GmailRequirementGroup:
    normalized_group_email = normalize_google_group_email(value)
    if not normalized_group_email:
        raise HTTPException(status_code=400, detail="Could not normalize this group value into a Google Groups address.")
    group_slug = normalize_google_group_slug(value) or normalized_group_email.split("@", 1)[0]
    existing = (
        db.query(GmailRequirementGroup)
        .filter(
            GmailRequirementGroup.owner_id == settings.owner_id,
            GmailRequirementGroup.normalized_group_email == normalized_group_email,
        )
        .first()
    )
    if existing:
        if display_name is not None and display_name.strip():
            existing.display_name = display_name.strip()
        existing.group_email = normalized_group_email
        existing.group_slug = group_slug
        existing.enabled = enabled
        db.commit()
        db.refresh(existing)
        return existing
    row = GmailRequirementGroup(
        owner_id=settings.owner_id,
        display_name=canonical_group_display_name(normalized_group_email, display_name),
        group_email=normalized_group_email,
        normalized_group_email=normalized_group_email,
        group_slug=group_slug,
        enabled=enabled,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _repair_unknown_role_drafts(db: Session, emails: list[RecruiterEmail]) -> None:
    _get_candidate_runtime_service().repair_unknown_role_drafts(db, emails)


def _refresh_unconfirmed_routing(db: Session, emails: list[RecruiterEmail]) -> None:
    _get_candidate_runtime_service().refresh_unconfirmed_routing(db, emails)


def _hydrate_candidates_for_review(db: Session, emails: list[RecruiterEmail]) -> None:
    if not emails:
        return
    _repair_unknown_role_drafts(db, emails)
    _refresh_unconfirmed_routing(db, emails)
    _fill_missing_gmail_rfc_ids(db, emails)


def _serialize_candidate_for_review(db: Session, email: RecruiterEmail) -> EmailResponse:
    _hydrate_candidates_for_review(db, [email])
    payload = EmailResponse.model_validate(email).model_dump()
    payload["attachment_file_names"] = _enabled_attachment_file_names(db)
    payload["parser_details"] = email.parser_details_json
    return EmailResponse.model_validate(payload)


def _get_candidate_for_review(db: Session, email_id: int) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return email


def _get_candidate_review(email_id: int, db: Session) -> EmailResponse:
    return _serialize_candidate_for_review(db, _get_candidate_for_review(db, email_id))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    return _settings_response_from_model(s)


@app.get("/settings/bootstrap", response_model=SettingsBootstrapResponse)
def get_settings_bootstrap(db: Session = Depends(get_db)) -> SettingsBootstrapResponse:
    user_settings = _get_settings(db)
    return SettingsBootstrapResponse(
        settings=_settings_response_from_model(user_settings),
        gmail_requirement_groups=[_gmail_requirement_group_response(item) for item in _list_gmail_requirement_groups(db)],
        resumes=[ResumeResponse.model_validate(item) for item in _list_resumes(db)],
        attachments=[AttachmentAssetResponse.model_validate(item) for item in _list_attachment_assets(db)],
        pending_skills=_list_pending_unknown_skills(db),
        pending_job_intent_signals=[
            _serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="pending")
        ],
        approved_job_intent_signals=[
            _serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="approved")
        ],
        loaded_at=datetime.now(UTC),
        owner_id=user_settings.owner_id,
    )


@app.put("/settings", response_model=SettingsResponse)
def update_settings(payload: SettingsRequest, db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    s.enabled = payload.enabled
    s.gmail_query = payload.gmail_query
    s.default_gmail_query = payload.default_gmail_query.strip() if payload.default_gmail_query.strip() else (payload.gmail_query.strip() or "is:unread in:inbox recruiter")
    s.saved_gmail_queries_json = json.dumps(sanitize_saved_queries(payload.saved_gmail_queries), separators=(",", ":"))
    s.mail_date = payload.mail_date
    s.default_date_mode = policy_service.normalize_default_date_mode(payload.default_date_mode)
    s.min_salary = payload.min_salary
    s.accepted_locations = _to_csv(payload.accepted_locations)
    s.visa_required_allowed = payload.visa_required_allowed
    s.remote_preference = payload.remote_preference
    s.role_keywords = _to_csv(payload.role_keywords)
    s.must_have_skills = _to_csv(payload.must_have_skills)
    s.employer_domains = _to_csv(sorted(normalize_employer_domains(payload.employer_domains)))
    s.free_text_guidance = payload.free_text_guidance
    s.qualification_threshold = payload.qualification_threshold
    s.feature_auto_polling = payload.feature_auto_polling
    s.feature_auto_poll_interval_minutes = max(1, min(int(payload.feature_auto_poll_interval_minutes), 1440))
    s.feature_nvoids_enabled = payload.feature_nvoids_enabled
    s.feature_nvoids_auto_sync = payload.feature_nvoids_auto_sync
    s.feature_nvoids_poll_interval_minutes = max(1, min(int(payload.feature_nvoids_poll_interval_minutes), 1440))
    s.nvoids_batch_limit = max(1, min(int(payload.nvoids_batch_limit), 50))
    s.nvoids_detail_title_mode = payload.nvoids_detail_title_mode
    s.nvoids_locations = _to_csv(payload.nvoids_locations)
    s.feature_auto_send = payload.feature_auto_send
    s.feature_retry_queue = payload.feature_retry_queue
    s.feature_ai_enabled = payload.feature_ai_enabled
    s.feature_ai_extractor_enabled = payload.feature_ai_extractor_enabled
    s.feature_semantic_enabled = payload.feature_semantic_enabled
    s.feature_groq_job_parser_enabled = payload.feature_groq_job_parser_enabled
    s.feature_gmail_requirement_groups_enabled = payload.feature_gmail_requirement_groups_enabled
    s.draft_text_size = normalize_draft_text_size(payload.draft_text_size)
    s.fallback_draft_template = payload.fallback_draft_template.strip() if payload.fallback_draft_template.strip() else DEFAULT_FALLBACK_DRAFT_TEMPLATE
    s.signature_name = payload.signature_name.strip() if payload.signature_name.strip() else DEFAULT_SIGNATURE_NAME
    s.signature_phone = payload.signature_phone.strip() if payload.signature_phone.strip() else DEFAULT_SIGNATURE_PHONE
    s.signature_email = payload.signature_email.strip() if payload.signature_email.strip() else DEFAULT_SIGNATURE_EMAIL
    s.preferred_employer_cc_email = (payload.preferred_employer_cc_email or "").strip().lower()
    s.resume_display_name = payload.resume_display_name.strip()
    normalized_policy = policy_service.normalize_policy(
        payload.policy if payload.policy is not None else policy_service.read_policy_from_settings(s.policy_json)
    )
    s.policy_json = json.dumps(normalized_policy, separators=(",", ":"))
    db.commit()
    db.refresh(s)
    return _settings_response_from_model(s)


@app.get("/settings/gmail-groups", response_model=list[GmailRequirementGroupResponse])
def list_gmail_requirement_groups(db: Session = Depends(get_db)) -> list[GmailRequirementGroupResponse]:
    return [_gmail_requirement_group_response(item) for item in _list_gmail_requirement_groups(db)]


@app.post("/settings/gmail-groups", response_model=GmailRequirementGroupResponse)
def create_gmail_requirement_group(payload: GmailRequirementGroupCreateRequest, db: Session = Depends(get_db)) -> GmailRequirementGroupResponse:
    row = _create_gmail_requirement_group(
        db,
        value=payload.value,
        display_name=payload.display_name,
        enabled=payload.enabled,
    )
    return _gmail_requirement_group_response(row)


@app.post("/settings/gmail-groups/bulk", response_model=list[GmailRequirementGroupResponse])
def bulk_create_gmail_requirement_groups(
    payload: GmailRequirementGroupBulkCreateRequest,
    db: Session = Depends(get_db),
) -> list[GmailRequirementGroupResponse]:
    rows: list[GmailRequirementGroupResponse] = []
    for normalized_group_email, display_name in parse_group_inputs(payload.values):
        row = _create_gmail_requirement_group(
            db,
            value=normalized_group_email,
            display_name=display_name,
            enabled=True,
        )
        rows.append(_gmail_requirement_group_response(row))
    return rows


@app.patch("/settings/gmail-groups/{group_id}", response_model=GmailRequirementGroupResponse)
def update_gmail_requirement_group(
    group_id: int,
    payload: GmailRequirementGroupUpdateRequest,
    db: Session = Depends(get_db),
) -> GmailRequirementGroupResponse:
    row = (
        db.query(GmailRequirementGroup)
        .filter(
            GmailRequirementGroup.owner_id == settings.owner_id,
            GmailRequirementGroup.id == group_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Gmail requirement group not found")
    if payload.display_name is not None:
        normalized_display_name = payload.display_name.strip()
        row.display_name = normalized_display_name or canonical_group_display_name(row.group_email, None)
    if payload.enabled is not None:
        row.enabled = payload.enabled
    db.commit()
    db.refresh(row)
    return _gmail_requirement_group_response(row)


@app.delete("/settings/gmail-groups/{group_id}", response_model=GmailRequirementGroupResponse)
def delete_gmail_requirement_group(group_id: int, db: Session = Depends(get_db)) -> GmailRequirementGroupResponse:
    row = (
        db.query(GmailRequirementGroup)
        .filter(
            GmailRequirementGroup.owner_id == settings.owner_id,
            GmailRequirementGroup.id == group_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Gmail requirement group not found")
    response = _gmail_requirement_group_response(row)
    db.delete(row)
    db.commit()
    return response


@app.post("/settings/resume", response_model=ResumeResponse)
def upload_resume(
    file: UploadFile = File(...),
    skills_text: str = Form(""),
    db: Session = Depends(get_db),
) -> ResumeResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="File name required")
    content = file.file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file not allowed")

    sha256 = hashlib.sha256(content).hexdigest()
    Path(settings.resume_storage_dir).mkdir(parents=True, exist_ok=True)
    target_path = Path(settings.resume_storage_dir) / f"{sha256}_{file.filename}"
    target_path.write_bytes(content)

    current = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .all()
    )
    for item in current:
        item.is_current = False

    last_version = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id)
        .order_by(ResumeAsset.version.desc())
        .first()
    )
    next_version = 1 if not last_version else last_version.version + 1

    resume = ResumeAsset(
        owner_id=settings.owner_id,
        file_path=str(target_path),
        file_name=file.filename,
        mime_type=file.content_type or "application/pdf",
        sha256=sha256,
        version=next_version,
        skills_text=_normalize_resume_skills_text(skills_text),
        is_enabled=True,
        is_current=True,
    )
    _refresh_resume_embedding(resume)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return ResumeResponse.model_validate(resume)


@app.get("/settings/resumes", response_model=list[ResumeResponse])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeAsset]:
    return _list_resumes(db)


@app.patch("/settings/resumes/{resume_id}", response_model=ResumeResponse)
def update_resume(resume_id: int, payload: ResumeUpdateRequest, db: Session = Depends(get_db)) -> ResumeResponse:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    if payload.is_enabled is None and payload.skills_text is None:
        raise HTTPException(status_code=400, detail="At least one resume update field is required")

    if payload.skills_text is not None:
        resume.skills_text = _normalize_resume_skills_text(payload.skills_text)
        _refresh_resume_embedding(resume)

    if payload.is_enabled is not None:
        resume.is_enabled = payload.is_enabled
        if payload.is_enabled:
            _set_legacy_current_resume(db, target_resume=resume)
        elif resume.is_current:
            replacement = _most_recent_enabled_resume(db, exclude_resume_id=resume.id)
            resume.is_current = False
            _set_legacy_current_resume(db, target_resume=replacement)
    db.commit()
    db.refresh(resume)
    return ResumeResponse.model_validate(resume)


@app.delete("/settings/resumes/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    file_path = Path(resume.file_path)
    deleted_was_current = bool(resume.is_current)
    db.delete(resume)
    db.flush()
    if deleted_was_current:
        replacement = _most_recent_enabled_resume(db, exclude_resume_id=resume_id)
        _set_legacy_current_resume(db, target_resume=replacement)
    db.commit()
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Resume deleted from database but not disk: {exc}") from exc
    return {"id": resume_id, "deleted": True}


@app.post("/settings/attachments", response_model=list[AttachmentAssetResponse])
def upload_attachment_files(files: list[UploadFile] = File(...), db: Session = Depends(get_db)) -> list[AttachmentAssetResponse]:
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    Path(settings.attachment_storage_dir).mkdir(parents=True, exist_ok=True)
    created: list[AttachmentAsset] = []
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=400, detail="File name required")
        content = file.file.read()
        if not content:
            raise HTTPException(status_code=400, detail=f"Empty file not allowed: {file.filename}")
        sha256 = hashlib.sha256(content).hexdigest()
        target_path = Path(settings.attachment_storage_dir) / f"{sha256}_{uuid.uuid4().hex}_{file.filename}"
        target_path.write_bytes(content)
        created.append(
            AttachmentAsset(
                owner_id=settings.owner_id,
                file_path=str(target_path),
                file_name=file.filename,
                mime_type=file.content_type or "application/octet-stream",
                sha256=sha256,
                file_size=len(content),
                is_enabled=True,
            )
        )
    db.add_all(created)
    db.commit()
    for item in created:
        db.refresh(item)
    return [AttachmentAssetResponse.model_validate(item) for item in created]


@app.get("/settings/attachments", response_model=list[AttachmentAssetResponse])
def list_attachment_files(db: Session = Depends(get_db)) -> list[AttachmentAssetResponse]:
    return [AttachmentAssetResponse.model_validate(item) for item in _list_attachment_assets(db)]


@app.patch("/settings/attachments/{attachment_id}", response_model=AttachmentAssetResponse)
def update_attachment_file(
    attachment_id: int,
    payload: AttachmentAssetUpdateRequest,
    db: Session = Depends(get_db),
) -> AttachmentAssetResponse:
    attachment = (
        db.query(AttachmentAsset)
        .filter(AttachmentAsset.owner_id == settings.owner_id, AttachmentAsset.id == attachment_id)
        .first()
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    attachment.is_enabled = payload.is_enabled
    db.commit()
    db.refresh(attachment)
    return AttachmentAssetResponse.model_validate(attachment)


@app.delete("/settings/attachments/{attachment_id}")
def delete_attachment_file(attachment_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    attachment = (
        db.query(AttachmentAsset)
        .filter(AttachmentAsset.owner_id == settings.owner_id, AttachmentAsset.id == attachment_id)
        .first()
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found")
    file_path = Path(attachment.file_path)
    db.delete(attachment)
    db.commit()
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Attachment deleted from database but not disk: {exc}") from exc
    return {"id": attachment_id, "deleted": True}


@app.get("/settings/skills/pending", response_model=list[PendingSkillResponse])
def list_pending_skills(db: Session = Depends(get_db)) -> list[PendingSkillResponse]:
    return _list_pending_unknown_skills(db)


@app.get("/settings/skills/approved", response_model=list[CustomSkillTaxonomyEntryResponse])
def list_approved_skills(db: Session = Depends(get_db)) -> list[CustomSkillTaxonomyEntryResponse]:
    return [_serialize_custom_skill_entry(item) for item in _list_approved_custom_skill_entries(db)]


@app.post("/settings/skills/approve", response_model=CustomSkillTaxonomyEntryResponse)
def approve_skill(payload: ApproveSkillRequest, db: Session = Depends(get_db)) -> CustomSkillTaxonomyEntryResponse:
    entry = _upsert_custom_skill_entry(
        db,
        skill_name=payload.skill_name,
        canonical_name=payload.canonical_name,
        aliases=payload.aliases,
        category=payload.category,
        cluster_hint=payload.cluster_hint,
        status="approved",
    )
    return _serialize_custom_skill_entry(entry)


@app.post("/settings/skills/approve-all", response_model=BulkApproveSkillsResponse)
def approve_all_skills(db: Session = Depends(get_db)) -> BulkApproveSkillsResponse:
    pending = _list_pending_unknown_skills(db)
    approved_names: list[str] = []
    suppressed = _known_or_suppressed_pending_skill_keys(db)
    for item in pending:
        normalized = normalize_taxonomy_text(item.skill_name)
        if not normalized or normalized in suppressed:
            continue
        _upsert_custom_skill_entry(
            db,
            skill_name=item.skill_name,
            canonical_name=item.skill_name,
            aliases=[],
            category="custom",
            cluster_hint=None,
            status="approved",
            auto_commit=False,
        )
        approved_names.append(item.skill_name)
        suppressed.add(normalized)
    if approved_names:
        clear_skill_taxonomy_cache()
        db.commit()
    processed_count = len(pending)
    approved_count = len(approved_names)
    return BulkApproveSkillsResponse(
        processed_count=processed_count,
        approved_count=approved_count,
        skipped_count=max(0, processed_count - approved_count),
        approved_skill_names=approved_names,
    )


@app.post("/settings/skills/dismiss", response_model=CustomSkillTaxonomyEntryResponse)
def dismiss_skill(payload: DismissSkillRequest, db: Session = Depends(get_db)) -> CustomSkillTaxonomyEntryResponse:
    entry = _upsert_custom_skill_entry(
        db,
        skill_name=payload.skill_name,
        canonical_name=payload.canonical_name,
        aliases=[],
        category="custom",
        cluster_hint=None,
        status="dismissed",
    )
    return _serialize_custom_skill_entry(entry)


@app.get("/settings/job-intent-learning/pending", response_model=list[JobIntentTaxonomyEntryResponse])
def list_pending_job_intent_learning(db: Session = Depends(get_db)) -> list[JobIntentTaxonomyEntryResponse]:
    return [_serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="pending")]


@app.get("/settings/job-intent-learning/approved", response_model=list[JobIntentTaxonomyEntryResponse])
def list_approved_job_intent_learning(db: Session = Depends(get_db)) -> list[JobIntentTaxonomyEntryResponse]:
    return [_serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="approved")]


@app.post("/settings/job-intent-learning/approve", response_model=JobIntentTaxonomyEntryResponse)
def approve_job_intent_learning(
    payload: ApproveJobIntentSignalRequest,
    db: Session = Depends(get_db),
) -> JobIntentTaxonomyEntryResponse:
    entry = _upsert_job_intent_entry(
        db,
        phrase=payload.phrase,
        polarity=payload.polarity,
        status="approved",
    )
    return _serialize_job_intent_entry(entry)


@app.post("/settings/job-intent-learning/approve-all", response_model=BulkApproveJobIntentSignalsResponse)
def approve_all_job_intent_learning(db: Session = Depends(get_db)) -> BulkApproveJobIntentSignalsResponse:
    pending = _list_job_intent_entries(db, status="pending")
    approved_signals: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in pending:
        key = (item.normalized_phrase or normalize_job_intent_phrase(item.phrase), item.polarity)
        if not key[0] or not key[1] or key in seen:
            continue
        entry = _upsert_job_intent_entry(
            db,
            phrase=item.phrase,
            polarity=item.polarity,
            status="approved",
            auto_commit=False,
        )
        approved_signals.append({"phrase": entry.phrase, "polarity": entry.polarity})
        seen.add(key)
    if approved_signals:
        db.commit()
    processed_count = len(pending)
    approved_count = len(approved_signals)
    return BulkApproveJobIntentSignalsResponse(
        processed_count=processed_count,
        approved_count=approved_count,
        skipped_count=max(0, processed_count - approved_count),
        approved_signals=approved_signals,
    )


@app.post("/settings/job-intent-learning/dismiss", response_model=JobIntentTaxonomyEntryResponse)
def dismiss_job_intent_learning(
    payload: DismissJobIntentSignalRequest,
    db: Session = Depends(get_db),
) -> JobIntentTaxonomyEntryResponse:
    entry = _upsert_job_intent_entry(
        db,
        phrase=payload.phrase,
        polarity=payload.polarity,
        status="dismissed",
    )
    return _serialize_job_intent_entry(entry)


@app.get("/gmail/status", response_model=GmailStatusResponse)
def gmail_status() -> GmailStatusResponse:
    configured, authenticated, detail = gmail_auth_status()
    return GmailStatusResponse(
        configured=configured,
        authenticated=authenticated,
        token_path=settings.google_token_path,
        last_sync_at=last_gmail_sync_at,
        detail=detail,
    )


@app.get("/ai/status", response_model=AIStatusResponse)
def ai_status(db: Session = Depends(get_db)) -> AIStatusResponse:
    user_settings = _get_settings(db)
    connected = bool(settings.deepseek_api_key)
    configured = connected and bool(settings.deepseek_base_url) and bool(settings.deepseek_model_fast)
    detail = "Ready" if connected else "DeepSeek API key missing (set Deepseek_API_KEY)."
    embedding_provider = settings.effective_semantic_embedding_provider
    embedding_model = settings.effective_semantic_embedding_model
    embedding_configured = False
    embedding_connected = False
    embedding_runtime_healthy: bool | None = None
    embedding_detail = "Embedding provider not configured."
    if embedding_provider == "hash":
        embedding_configured = True
        embedding_connected = True
        embedding_detail = "Ready (local hash embeddings)."
    elif embedding_provider == "sbert":
        embedding_configured = True
        embedding_connected = True
        embedding_detail = f"Ready (local sbert primary; model={embedding_model}; fallback=hash)."
    else:
        embedding_configured = False
        embedding_connected = False
        embedding_detail = f"Unsupported embedding provider: {embedding_provider}"

    if embedding_last_success_at and (
        embedding_last_attempted_at is None or embedding_last_success_at >= embedding_last_attempted_at
    ):
        embedding_runtime_healthy = True
    elif embedding_last_error:
        embedding_runtime_healthy = False
    else:
        embedding_runtime_healthy = None

    # Runtime health is primary; config readiness is exposed separately for diagnosis.
    if embedding_runtime_healthy is True:
        embedding_connected = True
        embedding_detail = "Runtime healthy (last embedding succeeded)."
    elif embedding_runtime_healthy is False:
        embedding_connected = False
        embedding_detail = embedding_last_error or "Runtime unhealthy (last embedding failed)."
    elif not embedding_configured:
        embedding_connected = False
        # Keep config-oriented detail when runtime has no signal.
        embedding_detail = embedding_detail
    else:
        embedding_connected = False
        embedding_detail = f"No runtime signal yet (no embedding attempts in this process). {embedding_detail}"

    groq_configured = bool(settings.groq_api_key) and bool(settings.groq_base_url) and bool(settings.groq_gate_model)
    groq_enabled_in_settings = bool(user_settings.feature_groq_job_parser_enabled)
    groq_request_mode = runtime_state.groq_request_mode or groq_request_mode_for_model(settings.groq_gate_model)
    groq_runtime_healthy: bool | None
    if runtime_state.groq_last_success_at and (
        runtime_state.groq_last_attempted_at is None
        or runtime_state.groq_last_success_at >= runtime_state.groq_last_attempted_at
    ) and not runtime_state.groq_last_error:
        groq_runtime_healthy = True
    elif runtime_state.groq_last_error:
        groq_runtime_healthy = False
    else:
        groq_runtime_healthy = None

    if not groq_enabled_in_settings:
        groq_detail = "Groq smart job parser is turned off in settings."
    elif not groq_configured:
        groq_detail = "Groq is enabled in settings, but backend config is missing API key, model, or base URL."
    elif groq_runtime_healthy is True:
        groq_detail = f"Groq runtime healthy for recent Gmail intent-gate calls (mode: {groq_request_mode})."
    elif groq_runtime_healthy is False:
        groq_detail = (
            f"Groq fallback active due to recent runtime failure: {runtime_state.groq_last_error} (mode: {groq_request_mode})."
            if runtime_state.groq_last_error
            else f"Groq fallback active due to a recent runtime failure (mode: {groq_request_mode})."
        )
    elif groq_request_mode == "json_object":
        groq_detail = "Groq is configured in json_object compatibility mode for the current model."
    else:
        groq_detail = "Groq is configured in structured json_schema mode, but no Groq attempt has been recorded in this process yet."

    return AIStatusResponse(
        configured=configured,
        connected=connected,
        running=ai_running,
        provider="deepseek",
        model=settings.deepseek_model_fast or "deepseek-chat",
        detail=detail,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_connected=embedding_connected,
        embedding_detail=embedding_detail,
        embedding_configured=embedding_configured,
        embedding_runtime_healthy=embedding_runtime_healthy,
        embedding_last_error=embedding_last_error,
        embedding_last_attempted_at=embedding_last_attempted_at,
        embedding_last_success_at=embedding_last_success_at,
        embedding_last_duration_ms=embedding_last_duration_ms,
        groq_configured=groq_configured,
        groq_enabled_in_settings=groq_enabled_in_settings,
        groq_model=(settings.groq_gate_model or "llama-3.1-8b-instant"),
        groq_base_url_present=bool(settings.groq_base_url),
        groq_runtime_healthy=groq_runtime_healthy,
        groq_last_error=runtime_state.groq_last_error,
        groq_detail=groq_detail,
        groq_request_mode=groq_request_mode,
        groq_last_attempted_at=runtime_state.groq_last_attempted_at,
        groq_last_success_at=runtime_state.groq_last_success_at,
        groq_last_duration_ms=runtime_state.groq_last_duration_ms,
        semantic_input_source=semantic_input_source,
        semantic_input_chars=semantic_input_chars,
        semantic_chunks=semantic_chunks,
        semantic_fallback_reason=semantic_fallback_reason,
        keyword_source=keyword_source,
        thread_snapshot_used=thread_snapshot_used,
        thread_snapshot_email_id=thread_snapshot_email_id,
        last_error=ai_last_error,
        last_started_at=ai_last_started_at,
        last_finished_at=ai_last_finished_at,
        last_duration_ms=ai_last_duration_ms,
        last_draft_source=ai_last_draft_source,
    )


@app.get("/telegram/status", response_model=TelegramStatusResponse)
def telegram_status() -> TelegramStatusResponse:
    if not telegram_service:
        configured_ids = TelegramRuntime.parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)
        enabled = bool((settings.telegram_bot_token or "").strip())
        detail = "Telegram bot disabled"
        if enabled and not configured_ids:
            detail = "TELEGRAM_ALLOWED_CHAT_IDS is empty"
        elif enabled:
            detail = "Telegram bot not initialized"
        return TelegramStatusResponse(
            enabled=enabled,
            polling=False,
            alerts_enabled=settings.telegram_alerts_enabled,
            authorized_chats=len(configured_ids),
            detail=detail,
        )
    status = telegram_service.status()
    return TelegramStatusResponse(
        enabled=status.enabled,
        polling=status.polling,
        alerts_enabled=status.alerts_enabled,
        authorized_chats=status.authorized_chats,
        detail=status.detail,
    )


@app.post("/analytics/events/view", response_model=ProductivityEventResponse)
def create_view_event(payload: ProductivityEventCreateRequest, db: Session = Depends(get_db)) -> ProductivityEventResponse:
    if payload.event_type not in ALLOWED_VIEW_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported view event_type")
    event_source = payload.event_source or "ui"
    try:
        event = _record_productivity_event(
            db,
            event_type=payload.event_type,
            event_source=event_source,
            entity_id=payload.entity_id,
            metadata=payload.metadata,
        )
    except OperationalError as exc:
        db.rollback()
        if "database is locked" not in str(exc).lower():
            raise
        now = datetime.now(UTC)
        return ProductivityEventResponse(
            id=0,
            owner_id=settings.owner_id,
            event_type=payload.event_type,
            event_source=event_source,
            entity_id=payload.entity_id,
            weight=EVENT_WEIGHTS.get(payload.event_type, 0.0),
            metadata=payload.metadata,
            occurred_at=now,
            created_at=now,
        )
    return _event_response(event)


@app.get("/analytics/events", response_model=list[ProductivityEventResponse])
def list_productivity_events(
    range: str = Query("current_day"),
    limit: int = Query(80, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ProductivityEventResponse]:
    if range not in RANGE_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid range")
    start, end = _range_bounds(range)
    rows = (
        db.query(ProductivityEvent)
        .filter(ProductivityEvent.owner_id == settings.owner_id)
        .filter(ProductivityEvent.occurred_at >= start, ProductivityEvent.occurred_at <= end)
        .order_by(ProductivityEvent.occurred_at.desc())
        .limit(limit)
        .all()
    )
    return [_event_response(row) for row in rows]


@app.get("/analytics/trend", response_model=ProductivityTrendResponse)
def productivity_trend(
    range: str = Query("current_day"),
    bucket: str | None = Query(None),
    db: Session = Depends(get_db),
) -> ProductivityTrendResponse:
    if range not in RANGE_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid range")
    resolved_bucket = bucket or _default_bucket_for_range(range)
    if resolved_bucket not in BUCKET_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid bucket")

    start, end = _range_bounds(range)
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    rows = (
        db.query(ProductivityEvent)
        .filter(ProductivityEvent.owner_id == settings.owner_id)
        .filter(ProductivityEvent.occurred_at >= start, ProductivityEvent.occurred_at <= end)
        .order_by(ProductivityEvent.occurred_at.asc())
        .all()
    )

    grouped: dict[datetime, dict[str, int]] = {}
    for row in rows:
        ts = _bucket_start(_ensure_utc(row.occurred_at), resolved_bucket)
        if ts not in grouped:
            grouped[ts] = {
                "sent_count": 0,
                "failed_count": 0,
                "needs_review_count": 0,
                "recent_run_count": 0,
            }
        if row.event_type == "approved_sent":
            grouped[ts]["sent_count"] += 1
        elif row.event_type == "failed_mapping_marked":
            grouped[ts]["failed_count"] += 1
        elif row.event_type == "needs_review_marked":
            grouped[ts]["needs_review_count"] += 1
        elif row.event_type == "recent_run_recorded":
            grouped[ts]["recent_run_count"] += 1

    bars: list[ProductivityBarPoint] = []
    cursor = _bucket_start(start, resolved_bucket)
    end_bucket = _bucket_start(end, resolved_bucket)
    while cursor <= end_bucket:
        payload = grouped.get(
            cursor,
            {
                "sent_count": 0,
                "failed_count": 0,
                "needs_review_count": 0,
                "recent_run_count": 0,
            },
        )
        bars.append(
            ProductivityBarPoint(
                ts=cursor,
                sent_count=payload["sent_count"],
                failed_count=payload["failed_count"],
                needs_review_count=payload["needs_review_count"],
                recent_run_count=payload["recent_run_count"],
            )
        )
        cursor = _next_bucket(cursor, resolved_bucket)

    current_total_sent = sum(point.sent_count for point in bars)

    if current_total_sent == 0 and all(
        point.failed_count == 0 and point.needs_review_count == 0 and point.recent_run_count == 0
        for point in bars
    ):
        return ProductivityTrendResponse(
            range=range,
            bucket=resolved_bucket,
            trend_direction="flat",
            trend_delta_pct=0.0,
            kpi_total_sent=0,
            previous_period_total_sent=0,
            bars=bars,
        )

    duration = end - start
    prev_start = start - duration
    prev_end = start
    prev_rows = (
        db.query(ProductivityEvent)
        .filter(ProductivityEvent.owner_id == settings.owner_id)
        .filter(ProductivityEvent.event_type == "approved_sent")
        .filter(ProductivityEvent.occurred_at >= prev_start, ProductivityEvent.occurred_at < prev_end)
        .all()
    )
    previous_total_sent = len(prev_rows)
    delta = current_total_sent - previous_total_sent
    direction = "flat"
    if delta > 0:
        direction = "up"
    elif delta < 0:
        direction = "down"
    base = float(max(previous_total_sent, 1))
    delta_pct = round((delta / base) * 100, 2)

    return ProductivityTrendResponse(
        range=range,
        bucket=resolved_bucket,
        trend_direction=direction,
        trend_delta_pct=delta_pct,
        kpi_total_sent=current_total_sent,
        previous_period_total_sent=previous_total_sent,
        bars=bars,
    )


@app.post("/gmail/sync", response_model=GmailSyncResponse)
def gmail_sync(db: Session = Depends(get_db)) -> GmailSyncResponse:
    return _get_orchestration_service().sync_gmail(db)


@app.post("/gmail/oauth/start", response_model=OAuthStartResponse)
def gmail_oauth_start() -> OAuthStartResponse:
    status, detail, authorization_url = start_oauth_bootstrap()
    configured, authenticated, _ = gmail_auth_status()
    return OAuthStartResponse(
        status=status,
        detail=detail,
        configured=configured,
        authenticated=authenticated,
        authorization_url=authorization_url,
    )


@app.get("/gmail/oauth/url", response_model=OAuthUrlResponse)
def gmail_oauth_url() -> OAuthUrlResponse:
    return OAuthUrlResponse(authorization_url=oauth_authorization_url())


@app.post("/gmail/labeling/preview", response_model=GmailLabelingPreviewResponse)
def gmail_labeling_preview(payload: GmailLabelingPreviewRequest) -> GmailLabelingPreviewResponse:
    service = _ensure_gmail_labeling_service()
    decision = service.decide_label(
        LabelRuleInput(
            sender=payload.sender,
            subject=payload.subject,
            body=payload.body,
            state=payload.state,
            decision=payload.decision,
            routing_status=payload.routing_status,
            routing_confidence=float(payload.routing_confidence or 0.0),
            skip_reason=payload.skip_reason,
            draft_reply=payload.draft_reply,
        )
    )
    return GmailLabelingPreviewResponse(label=decision.label, reason_path=decision.reason_path)


def _recruiter_opportunity_response(row: RecruiterOpportunity, recruiter: RecruiterNumber | None) -> RecruiterOpportunityResponse:
    return RecruiterOpportunityResponse(
        id=row.id,
        recruiter_number_id=row.recruiter_number_id,
        source_email_id=row.source_email_id,
        gmail_message_id=row.gmail_message_id,
        source_type=row.source_type or "gmail",
        source_url=row.source_url,
        external_opportunity_id=row.external_opportunity_id,
        email_subject=row.email_subject,
        email_sender=row.email_sender,
        gmail_open_url=row.gmail_open_url,
        received_at=row.received_at,
        job_title=row.job_title,
        client=row.client,
        location=row.location,
        work_mode=row.work_mode,
        visa_restrictions=row.visa_restrictions,
        extracted_skills=row.extracted_skills,
        evidence=row.evidence,
        recruiter_name=(recruiter.recruiter_name if recruiter else ""),
        recruiter_email=(recruiter.recruiter_email if recruiter else ""),
        recruiter_phone_display=(recruiter.display_phone_number if recruiter else ""),
        recruiter_phone_normalized=(recruiter.normalized_phone_number if recruiter else ""),
        status=row.status,
        notes=row.notes,
        cold_call_script=row.cold_call_script,
        cold_call_script_updated_at=row.cold_call_script_updated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@app.post("/automation/run-once", response_model=AutomationRunResponse)
def automation_run_once(payload: AutomationRunRequest | None = None, db: Session = Depends(get_db)) -> AutomationRunResponse:
    return _get_orchestration_service().run_once(payload, db)


@app.post("/phase0/emails/ingest", response_model=EmailResponse)
def ingest_email(payload: IngestEmailRequest, db: Session = Depends(get_db)) -> RecruiterEmail:
    user_settings = _get_settings(db)
    parsed, parser_details = parse_email_with_details(payload.subject, payload.body, source="manual")
    effective_policy = policy_service.read_policy_from_settings(user_settings.policy_json)
    hard_pass, hard_reason = hard_filter_check(parsed, user_settings, effective_policy)
    active_resume = _active_resume(db)
    resume_selection = _select_best_resume_match(
        subject=payload.subject,
        body=payload.body,
        parsed=parsed,
        parser_details=parser_details,
        user_settings=user_settings,
        email_row=None,
        db=db,
        owner_id=settings.owner_id,
        external_thread_id=None,
    )
    selected_resume = cast(ResumeAsset | None, getattr(resume_selection, "resume", None)) or active_resume
    ai_score = cast(float, getattr(resume_selection, "ai_score"))
    ai_summary = cast(str, getattr(resume_selection, "ai_summary"))
    ai_score_source = cast(str, getattr(resume_selection, "ai_score_source"))
    ats_score = cast(float | None, getattr(resume_selection, "ats_score", None))
    ats_summary = cast(str | None, getattr(resume_selection, "ats_summary", None))
    ats_score_source = cast(str | None, getattr(resume_selection, "ats_score_source", None))
    ats_breakdown_json = cast(str | None, getattr(resume_selection, "ats_breakdown_json", None))
    resume_picker_score = cast(float | None, getattr(resume_selection, "final_resume_score", None))
    resume_picker_reason = cast(str | None, getattr(resume_selection, "selection_reason", None))
    resume_picker_candidates_json = cast(str | None, getattr(resume_selection, "candidate_rankings_json", None))
    resume_picker_breakdown_json = cast(str | None, getattr(resume_selection, "picker_breakdown_json", None))
    email_embedding_json = cast(str | None, getattr(resume_selection, "email_embedding_json"))
    resume_embedding_json = cast(str | None, getattr(resume_selection, "resume_embedding_json"))
    semantic_diag = getattr(resume_selection, "semantic_diag")
    threshold = policy_service.policy_threshold(user_settings.qualification_threshold, effective_policy)
    score_mode = policy_service.draft_rule_mode(effective_policy, "score_threshold")
    score_blocked = score_mode == "block" and ai_score < threshold
    warnings: list[str] = []
    if hard_pass and hard_reason.startswith("warnings: "):
        warnings.append(hard_reason.removeprefix("warnings: ").strip())
    if score_mode == "warn" and ai_score < threshold:
        warnings.append(f"score_below_threshold:{ai_score:.2f}<{threshold:.2f}")
    state = "needs_review" if hard_pass and not score_blocked else "auto_rejected"
    decision = "Qualified" if state == "needs_review" else "Reject"
    fallback_draft = _build_user_fallback_draft(
        db,
        user_settings,
        sender=payload.sender,
        role=str(parsed["role"]),
        parsed=parsed,
        greeting_line=greeting_from_to_contact(None, payload.body),
        resume_file_name=resolve_resume_display_name(user_settings, selected_resume.file_name if selected_resume else None),
    )

    email = RecruiterEmail(
        owner_id=settings.owner_id,
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        role=str(parsed["role"]),
        location=str(parsed["location"]),
        salary_text=str(parsed["salary_text"]),
        skills_text=str(parsed["skills_text"]),
        skills_json=json.dumps(
            build_skills_json_payload(parser_details, fallback_skills_text=str(parsed["skills_text"])),
            separators=(",", ":"),
        ),
        score=int(ai_score * 100),
        decision=decision,
        state=state,
        decision_reason="manual_ingest_with_warnings" if state == "needs_review" and warnings else "manual_ingest",
        hard_filter_result=policy_service.combine_rule_messages(warnings) if state == "needs_review" else hard_reason,
        auto_reject_reason=None if state == "needs_review" else "manual_ingest_not_qualified",
        ai_score=ai_score,
        ai_score_source=ai_score_source,
        ai_summary=ai_summary,
        ats_score=ats_score,
        ats_score_source=ats_score_source,
        ats_summary=ats_summary,
        ats_breakdown_json=ats_breakdown_json,
        resume_picker_score=resume_picker_score,
        resume_picker_reason=resume_picker_reason,
        resume_picker_candidates_json=resume_picker_candidates_json,
        resume_picker_breakdown_json=resume_picker_breakdown_json,
        semantic_input_source=getattr(semantic_diag, "input_source", None),
        semantic_input_chars=getattr(semantic_diag, "input_chars", None),
        semantic_chunks=getattr(semantic_diag, "chunks", None),
        semantic_fallback_reason=getattr(semantic_diag, "fallback_reason", None),
        keyword_source=getattr(semantic_diag, "keyword_source", None),
        thread_snapshot_used=getattr(semantic_diag, "thread_snapshot_used", None),
        thread_snapshot_email_id=getattr(semantic_diag, "thread_snapshot_email_id", None),
        semantic_embedding=email_embedding_json,
        resume_asset_id=selected_resume.id if selected_resume else None,
        resume_file_name=selected_resume.file_name if selected_resume else None,
        draft_reply=fallback_draft
        if state == "needs_review"
        else "",
        draft_source="rules_only" if state == "needs_review" else None,
        draft_model=None,
        draft_ai_error=None,
        draft_resume_context_status=RESUME_CONTEXT_RULES_ONLY if state == "needs_review" else None,
        approval_status="pending",
        sent_status="not_sent",
        source="manual",
        parser_details_json=json.dumps(parser_details, separators=(",", ":")),
    )
    db.add(email)
    if selected_resume and resume_embedding_json and selected_resume.semantic_embedding != resume_embedding_json:
        selected_resume.semantic_embedding = resume_embedding_json
    db.commit()
    db.refresh(email)
    if state == "needs_review":
        _record_productivity_event(
            db,
            event_type="needs_review_marked",
            event_source="state",
            entity_id=email.id,
            metadata={"source": "manual_ingest"},
        )
    return email


@app.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    state: str = Query("needs_review"),
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("newest"),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> CandidateListResponse:
    states = [s.strip() for s in state.split(",") if s.strip()]
    query = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id)
    if states:
        query = query.filter(or_(*[RecruiterEmail.state == s for s in states]))

    if mail_date:
        try:
            selected = date.fromisoformat(mail_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="mail_date must be a valid YYYY-MM-DD date") from exc
        start, end = _mail_date_utc_window(selected)
        field_name = _mail_date_filter_field(states)
        if field_name == "sent_at":
            query = query.filter(RecruiterEmail.sent_at.is_not(None))
            query = query.filter(RecruiterEmail.sent_at >= start, RecruiterEmail.sent_at < end)
        else:
            query = query.filter(
                or_(
                    (RecruiterEmail.source == "gmail")
                    & RecruiterEmail.gmail_received_at.is_not(None)
                    & (RecruiterEmail.gmail_received_at >= start)
                    & (RecruiterEmail.gmail_received_at < end),
                    (RecruiterEmail.source != "gmail")
                    & (RecruiterEmail.created_at >= start)
                    & (RecruiterEmail.created_at < end),
                )
            )

    if sort == "highest_score":
        query = query.order_by(RecruiterEmail.score.desc(), RecruiterEmail.created_at.desc())
    elif _is_approved_sent_only(states):
        query = query.order_by(
            RecruiterEmail.sent_at.is_(None),
            RecruiterEmail.sent_at.desc(),
            RecruiterEmail.created_at.desc(),
        )
    else:
        query = query.order_by(RecruiterEmail.created_at.desc())

    items = query.offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    _hydrate_candidates_for_review(db, visible)
    next_cursor = cursor + limit if has_next else None
    attachment_file_names = _enabled_attachment_file_names(db)
    return CandidateListResponse(
        items=[
            EmailResponse.model_validate(
                {
                    **EmailResponse.model_validate(item).model_dump(),
                    "attachment_file_names": attachment_file_names,
                    "parser_details": item.parser_details_json,
                }
            )
            for item in visible
        ],
        next_cursor=next_cursor,
        has_next=has_next,
    )


def _ensure_gmail_labeling_service() -> GmailLabelingService:
    return gmail_labeling_runtime_service.ensure_service()


def _build_label_rule_input_from_email(email: RecruiterEmail) -> LabelRuleInput:
    return gmail_labeling_runtime_service.build_label_rule_input(email)


def _apply_gmail_label_for_email(
    *,
    email: RecruiterEmail,
    candidate_item: GmailMessageCandidate | dict[str, object],
) -> None:
    gmail_labeling_runtime_service.apply_for_email(email=email, candidate_item=candidate_item)


def _log_gmail_labeling_stats() -> None:
    gmail_labeling_runtime_service.log_stats()


def _is_hidden_nvoids_placeholder_recruiter(row: RecruiterNumber | None) -> bool:
    if row is None:
        return False
    normalized = str(row.normalized_phone_number or "").strip().lower()
    display = str(row.display_phone_number or "").strip().lower()
    return normalized.startswith("nvoids-") and display == "unknown" and row.first_detected_email_id is None


def _is_hidden_invalid_employer_number(row: EmployerNumber | None) -> bool:
    if row is None:
        return False
    display = str(row.display_phone_number or "").strip()
    if not display or display.lower() == "unknown":
        return False
    normalized = str(row.normalized_phone_number or "").strip()
    return not canonicalize_phone(normalized) and not canonicalize_phone(display)


def _standardized_display_phone(raw: str, canonical: str, *, fallback: str = "Unknown") -> str:
    return best_display_phone(raw or canonical, fallback=fallback)


TItem = TypeVar("TItem")


def _paginate_items(items: list[TItem], *, cursor: int, limit: int) -> tuple[list[TItem], int | None, bool]:
    page = items[cursor : cursor + limit + 1]
    has_next = len(page) > limit
    visible = page[:limit]
    next_cursor = cursor + limit if has_next else None
    return visible, next_cursor, has_next


@app.get("/premium-numbers", response_model=PremiumNumberListResponse)
def list_premium_numbers(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    confidence: str | None = Query(default=None),
    recruiter_only: bool = Query(default=True),
    contact_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> PremiumNumberListResponse:
    query = db.query(PremiumNumberLead).filter(PremiumNumberLead.owner_id == settings.owner_id)
    if recruiter_only:
        query = query.filter(PremiumNumberLead.is_recruiter_relevant.is_(True))

    if confidence:
        normalized_confidence = confidence.strip().lower()
        if normalized_confidence in {"high", "medium", "low"}:
            query = query.filter(PremiumNumberLead.confidence == normalized_confidence)
    if contact_type:
        normalized_contact_type = contact_type.strip().lower()
        if normalized_contact_type in {"recruiter_direct", "submission_contact", "employer_internal", "unknown"}:
            query = query.filter(PremiumNumberLead.contact_type == normalized_contact_type)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                PremiumNumberLead.phone_number_display.ilike(like),
                PremiumNumberLead.owner_name.ilike(like),
                PremiumNumberLead.company.ilike(like),
                PremiumNumberLead.designation.ilike(like),
                PremiumNumberLead.purpose.ilike(like),
                PremiumNumberLead.source_email_sender.ilike(like),
                PremiumNumberLead.source_email_subject.ilike(like),
            )
        )

    if mail_date:
        try:
            selected = date.fromisoformat(mail_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="mail_date must be a valid YYYY-MM-DD date") from exc
        start, end = _mail_date_utc_window(selected)
        query = query.join(RecruiterEmail, RecruiterEmail.id == PremiumNumberLead.recruiter_email_id)
        query = query.filter(RecruiterEmail.gmail_received_at.is_not(None))
        query = query.filter(RecruiterEmail.gmail_received_at >= start, RecruiterEmail.gmail_received_at < end)

    query = query.order_by(PremiumNumberLead.recruiter_relevance_score.desc(), PremiumNumberLead.created_at.desc())
    items = query.offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    next_cursor = cursor + limit if has_next else None
    return PremiumNumberListResponse(
        items=[PremiumNumberResponse.model_validate(item) for item in visible],
        next_cursor=next_cursor,
        has_next=has_next,
    )


@app.get("/premium-numbers/{lead_id}", response_model=PremiumNumberResponse)
def get_premium_number(lead_id: int, db: Session = Depends(get_db)) -> PremiumNumberLead:
    lead = (
        db.query(PremiumNumberLead)
        .filter(PremiumNumberLead.owner_id == settings.owner_id, PremiumNumberLead.id == lead_id)
        .first()
    )
    if not lead:
        raise HTTPException(status_code=404, detail="Premium number lead not found")
    return lead


@app.post("/premium-numbers/reextract/{recruiter_email_id}", response_model=dict[str, int])
def reextract_premium_numbers(recruiter_email_id: int, db: Session = Depends(get_db)) -> dict[str, int]:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == recruiter_email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    workflow_result = _get_candidate_runtime_service().capture_premium_numbers(db, email)
    return {"stored_count": workflow_result.stored_count if workflow_result else 0}


@app.get("/number-review", response_model=UnknownNumberReviewCardListResponse)
def list_number_review_queue(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> UnknownNumberReviewCardListResponse:
    query = db.query(NumberReviewQueue).filter(
        NumberReviewQueue.owner_id == settings.owner_id,
        NumberReviewQueue.state == "pending",
    )
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                NumberReviewQueue.display_phone_number.ilike(like),
                NumberReviewQueue.owner_name.ilike(like),
                NumberReviewQueue.company.ilike(like),
                NumberReviewQueue.designation.ilike(like),
                NumberReviewQueue.email_sender.ilike(like),
                NumberReviewQueue.email_subject.ilike(like),
            )
        )
    items = query.order_by(NumberReviewQueue.created_at.desc()).offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    next_cursor = cursor + limit if has_next else None
    return UnknownNumberReviewCardListResponse(
        items=[UnknownNumberReviewCardResponse.model_validate(row) for row in visible],
        next_cursor=next_cursor,
        has_next=has_next,
    )


@app.post("/number-review/{review_id}/mark-recruiter", response_model=dict[str, int | str])
def mark_number_as_recruiter(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = (
        db.query(NumberReviewQueue)
        .filter(NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.id == review_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="Review card not found")
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    canonical_phone = canonicalize_phone(card.normalized_phone_number or card.display_phone_number)
    if not canonical_phone:
        raise HTTPException(status_code=422, detail="Invalid phone number on review card")

    recruiter = (
        db.query(RecruiterNumber)
        .filter(
            RecruiterNumber.owner_id == settings.owner_id,
            RecruiterNumber.normalized_phone_number == canonical_phone,
        )
        .first()
    )
    candidate_email = extract_email_address(card.email_sender or "")
    if not recruiter:
        recruiter = RecruiterNumber(
            owner_id=settings.owner_id,
            normalized_phone_number=canonical_phone,
            display_phone_number=_standardized_display_phone(
                card.display_phone_number or card.normalized_phone_number,
                canonical_phone,
            ),
            recruiter_name=card.owner_name or "Unknown",
            company=card.company or "Unknown",
            designation=card.designation or "Unknown",
            recruiter_email=candidate_email,
            first_detected_email_id=card.source_email_id,
        )
        db.add(recruiter)
        db.flush()
    else:
        # Preserve higher-quality existing identity; only enrich missing/unknown fields.
        if (not recruiter.recruiter_name or recruiter.recruiter_name.strip().lower() == "unknown") and card.owner_name:
            recruiter.recruiter_name = card.owner_name
        if (not recruiter.company or recruiter.company.strip().lower() == "unknown") and card.company:
            recruiter.company = card.company
        if (not recruiter.designation or recruiter.designation.strip().lower() == "unknown") and card.designation:
            recruiter.designation = card.designation
        if not recruiter.recruiter_email and candidate_email:
            recruiter.recruiter_email = candidate_email

    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == card.source_email_id)
        .first()
    )
    gmail_message_id = (email.external_message_id if email else None) or f"manual-{card.source_email_id}"
    existing_opportunity = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.recruiter_number_id == recruiter.id,
            RecruiterOpportunity.gmail_message_id == gmail_message_id,
        )
        .first()
    )
    if not existing_opportunity:
        db.add(
            RecruiterOpportunity(
                owner_id=settings.owner_id,
                recruiter_number_id=recruiter.id,
                source_email_id=card.source_email_id,
                gmail_message_id=gmail_message_id,
                email_subject=card.email_subject,
                email_sender=card.email_sender,
                gmail_open_url=card.gmail_open_url,
                received_at=email.gmail_received_at if email else datetime.now(UTC),
                job_title=email.role if email else card.email_subject,
                client="",
                location=email.location if email else "",
                work_mode="",
                visa_restrictions="",
                extracted_skills=email.skills_text if email else "",
                evidence=card.evidence_snippet,
                status="New",
                notes="",
            )
        )

    card.state = "classified_recruiter"
    db.commit()
    return {"review_id": card.id, "status": card.state}


@app.post("/number-review/{review_id}/mark-employer", response_model=dict[str, int | str])
def mark_number_as_employer(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = (
        db.query(NumberReviewQueue)
        .filter(NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.id == review_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="Review card not found")
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    canonical_phone = canonicalize_phone(card.normalized_phone_number or card.display_phone_number)
    if not canonical_phone:
        raise HTTPException(status_code=422, detail="Invalid phone number on review card")

    existing = (
        db.query(EmployerNumber)
        .filter(
            EmployerNumber.owner_id == settings.owner_id,
            EmployerNumber.normalized_phone_number == canonical_phone,
        )
        .first()
    )
    if not existing:
        db.add(
            EmployerNumber(
                owner_id=settings.owner_id,
                normalized_phone_number=canonical_phone,
                display_phone_number=_standardized_display_phone(
                    card.display_phone_number or card.normalized_phone_number,
                    canonical_phone,
                ),
                owner_name=card.owner_name,
                company=card.company,
                source_email_id=card.source_email_id,
            )
        )
    card.state = "classified_employer"
    db.commit()
    return {"review_id": card.id, "status": card.state}


@app.delete("/number-review/{review_id}", response_model=dict[str, int | str])
def delete_number_review_card(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = (
        db.query(NumberReviewQueue)
        .filter(NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.id == review_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="Review card not found")
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    card.state = "dismissed"
    db.commit()
    return {"review_id": card.id, "status": card.state}


@app.get("/recruiter-numbers", response_model=RecruiterNumberListResponse)
def list_recruiter_numbers(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RecruiterNumberListResponse:
    query = db.query(RecruiterNumber).filter(RecruiterNumber.owner_id == settings.owner_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                RecruiterNumber.display_phone_number.ilike(like),
                RecruiterNumber.recruiter_name.ilike(like),
                RecruiterNumber.company.ilike(like),
                RecruiterNumber.designation.ilike(like),
                RecruiterNumber.recruiter_email.ilike(like),
            )
        )
    rows = (
        query
        .order_by(RecruiterNumber.updated_at.desc())
        .all()
    )
    results: list[RecruiterNumberResponse] = []
    for row in rows:
        if _is_hidden_nvoids_placeholder_recruiter(row):
            continue
        total = (
            db.query(func.count(RecruiterOpportunity.id))
            .filter(
                RecruiterOpportunity.owner_id == settings.owner_id,
                RecruiterOpportunity.recruiter_number_id == row.id,
            )
            .scalar()
            or 0
        )
        last_received = (
            db.query(func.max(RecruiterOpportunity.received_at))
            .filter(
                RecruiterOpportunity.owner_id == settings.owner_id,
                RecruiterOpportunity.recruiter_number_id == row.id,
            )
            .scalar()
        )
        results.append(
            RecruiterNumberResponse(
                id=row.id,
                normalized_phone_number=row.normalized_phone_number,
                display_phone_number=row.display_phone_number,
                recruiter_name=row.recruiter_name,
                company=row.company,
                designation=row.designation,
                recruiter_email=row.recruiter_email,
                first_detected_email_id=row.first_detected_email_id,
                total_opportunity_count=int(total),
                last_email_received_at=last_received,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )
    visible, next_cursor, has_next = _paginate_items(results, cursor=cursor, limit=limit)
    return RecruiterNumberListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.post("/recruiter-numbers/{recruiter_number_id}/swap-to-employer", response_model=dict[str, int | str])
def swap_recruiter_number_to_employer(recruiter_number_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    recruiter = (
        db.query(RecruiterNumber)
        .filter(RecruiterNumber.owner_id == settings.owner_id, RecruiterNumber.id == recruiter_number_id)
        .first()
    )
    if not recruiter:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    canonical_phone = canonicalize_phone(recruiter.normalized_phone_number or recruiter.display_phone_number)
    if not canonical_phone:
        raise HTTPException(status_code=422, detail="Invalid recruiter phone number")

    employer = (
        db.query(EmployerNumber)
        .filter(
            EmployerNumber.owner_id == settings.owner_id,
            EmployerNumber.normalized_phone_number == canonical_phone,
        )
        .first()
    )
    if not employer:
        db.add(
            EmployerNumber(
                owner_id=settings.owner_id,
                normalized_phone_number=canonical_phone,
                display_phone_number=_standardized_display_phone(
                    recruiter.display_phone_number or recruiter.normalized_phone_number,
                    canonical_phone,
                ),
                owner_name=recruiter.recruiter_name or "Unknown",
                company=recruiter.company or "Unknown",
                source_email_id=recruiter.first_detected_email_id,
            )
        )

    db.delete(recruiter)
    db.commit()
    return {"id": recruiter_number_id, "swapped_to": "employer"}


@app.get("/employer-numbers", response_model=EmployerNumberListResponse)
def list_employer_numbers(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EmployerNumberListResponse:
    query = db.query(EmployerNumber).filter(EmployerNumber.owner_id == settings.owner_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                EmployerNumber.display_phone_number.ilike(like),
                EmployerNumber.owner_name.ilike(like),
                EmployerNumber.company.ilike(like),
            )
        )
    rows = (
        query
        .order_by(EmployerNumber.updated_at.desc())
        .all()
    )
    items = [EmployerNumberResponse.model_validate(row) for row in rows if not _is_hidden_invalid_employer_number(row)]
    visible, next_cursor, has_next = _paginate_items(items, cursor=cursor, limit=limit)
    return EmployerNumberListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.post("/employer-numbers/{employer_number_id}/swap-to-recruiter", response_model=dict[str, int | str])
def swap_employer_number_to_recruiter(employer_number_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    employer = (
        db.query(EmployerNumber)
        .filter(EmployerNumber.owner_id == settings.owner_id, EmployerNumber.id == employer_number_id)
        .first()
    )
    if not employer:
        raise HTTPException(status_code=404, detail="Employer number not found")
    canonical_phone = canonicalize_phone(employer.normalized_phone_number or employer.display_phone_number)
    if not canonical_phone:
        raise HTTPException(status_code=422, detail="Invalid employer phone number")

    recruiter = (
        db.query(RecruiterNumber)
        .filter(
            RecruiterNumber.owner_id == settings.owner_id,
            RecruiterNumber.normalized_phone_number == canonical_phone,
        )
        .first()
    )
    if not recruiter:
        db.add(
            RecruiterNumber(
                owner_id=settings.owner_id,
                normalized_phone_number=canonical_phone,
                display_phone_number=_standardized_display_phone(
                    employer.display_phone_number or employer.normalized_phone_number,
                    canonical_phone,
                ),
                recruiter_name=employer.owner_name or "Unknown",
                company=employer.company or "Unknown",
                designation="Unknown",
                recruiter_email="",
                first_detected_email_id=employer.source_email_id,
            )
        )

    db.delete(employer)
    db.commit()
    return {"id": employer_number_id, "swapped_to": "recruiter"}


@app.get("/recruiter-opportunities", response_model=RecruiterOpportunityListResponse)
def list_recruiter_opportunities(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> RecruiterOpportunityListResponse:
    query = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == settings.owner_id)
    if status and status in OPPORTUNITY_STATUS_VALUES:
        query = query.filter(RecruiterOpportunity.status == status)
    if source_type in {"gmail", "nvoids"}:
        query = query.filter(RecruiterOpportunity.source_type == source_type)
    if mail_date:
        selected = date.fromisoformat(mail_date)
        start, end = _mail_date_utc_window(selected)
        query = query.filter(RecruiterOpportunity.received_at.is_not(None))
        query = query.filter(RecruiterOpportunity.received_at >= start, RecruiterOpportunity.received_at < end)
    rows = query.order_by(RecruiterOpportunity.received_at.desc(), RecruiterOpportunity.created_at.desc()).all()
    recruiter_ids = sorted({row.recruiter_number_id for row in rows})
    recruiter_rows = (
        db.query(RecruiterNumber)
        .filter(RecruiterNumber.owner_id == settings.owner_id, RecruiterNumber.id.in_(recruiter_ids))
        .all()
        if recruiter_ids
        else []
    )
    recruiter_map = {row.id: row for row in recruiter_rows}
    items = [
        _recruiter_opportunity_response(row, recruiter)
        for row in rows
        for recruiter in [recruiter_map.get(row.recruiter_number_id)]
        if not _is_hidden_nvoids_placeholder_recruiter(recruiter)
    ]
    if q:
        needle = q.strip().lower()
        items = [
            item
            for item in items
            if needle in (item.email_subject or "").lower()
            or needle in (item.email_sender or "").lower()
            or needle in (item.job_title or "").lower()
            or needle in (item.client or "").lower()
            or needle in (item.location or "").lower()
            or needle in (item.extracted_skills or "").lower()
            or needle in (item.recruiter_name or "").lower()
            or needle in (item.recruiter_email or "").lower()
            or needle in (item.recruiter_phone_display or "").lower()
        ]
    visible, next_cursor, has_next = _paginate_items(items, cursor=cursor, limit=limit)
    return RecruiterOpportunityListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.post("/external-feeds/nvoids/sync", response_model=ExternalFeedSyncResponse)
def sync_external_nvoids(
    batch_limit: int | None = Query(default=None, ge=1, le=50),
    db: Session = Depends(get_db),
) -> ExternalFeedSyncResponse:
    user_settings = _get_settings(db)
    if not user_settings.feature_nvoids_enabled:
        raise HTTPException(status_code=400, detail="Nvoids sync is disabled in settings")
    resolved_batch_limit = batch_limit if batch_limit is not None else _nvoids_batch_limit(user_settings)
    if not telegram_action_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="another_run_in_progress")
    try:
        result = external_feed_service.sync_nvoids(
            db,
            owner_id=settings.owner_id,
            max_items=resolved_batch_limit,
        )
    except Exception as exc:
        logger.exception(
            "nvoids_sync_endpoint_failed owner_id=%r batch_limit=%s semantic_enabled=%s ai_enabled=%s",
            settings.owner_id,
            resolved_batch_limit,
            getattr(user_settings, "feature_semantic_enabled", None),
            getattr(user_settings, "feature_ai_enabled", None),
        )
        raise HTTPException(status_code=502, detail=f"nvoids_sync_failed: {exc}") from exc
    finally:
        telegram_action_lock.release()
    return ExternalFeedSyncResponse(
        source_type=result.source_type,
        run_key=result.run_key,
        fetched_count=result.fetched_count,
        created_count=result.created_count,
        deduped_count=result.deduped_count,
        failed_count=result.failed_count,
        skipped_location_count=result.skipped_location_count,
        run_id=result.run_id,
    )


@app.post("/external-feeds/nvoids/backfill-phones")
def backfill_external_nvoids_phones(
    limit: int = Query(default=5000, ge=1, le=50000),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    result = external_feed_service.backfill_nvoids_contact_phones(db, owner_id=settings.owner_id, limit=limit)
    return result


@app.get("/external-feeds/runs", response_model=list[ExternalScrapeRunResponse])
def list_external_feed_runs(limit: int = Query(default=20, ge=1, le=200), db: Session = Depends(get_db)) -> list[ExternalScrapeRunResponse]:
    rows = (
        db.query(ExternalScrapeRun)
        .filter(ExternalScrapeRun.owner_id == settings.owner_id)
        .order_by(ExternalScrapeRun.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        ExternalScrapeRunResponse(
            id=row.id,
            source_type=row.source_type,
            started_at=row.started_at,
            ended_at=row.ended_at,
            fetched_count=row.fetched_count,
            created_count=row.created_count,
            deduped_count=row.deduped_count,
            failed_count=row.failed_count,
            notes=row.notes,
        )
        for row in rows
    ]


@app.get("/recent-runs", response_model=RecentRunListResponse)
def list_recent_runs(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> RecentRunListResponse:
    query = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == settings.owner_id)
    )
    if mail_date:
        try:
            selected = date.fromisoformat(mail_date)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="mail_date must be a valid YYYY-MM-DD date") from exc
        start, end = _mail_date_utc_window(selected)
        query = query.filter(RecentRun.created_at >= start, RecentRun.created_at < end)
    rows = query.order_by(RecentRun.created_at.desc(), RecentRun.id.desc()).all()
    items = [_recent_run_response(row) for row in rows]
    visible, next_cursor, has_next = _paginate_items(items, cursor=cursor, limit=limit)
    return RecentRunListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.get("/recent-runs/{run_key}/items", response_model=RecentRunItemListResponse)
def list_recent_run_items(
    run_key: str,
    cursor: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    outcome: str = Query(default="skipped"),
    db: Session = Depends(get_db),
) -> RecentRunItemListResponse:
    query = (
        db.query(RecentRunSkippedItem)
        .filter(
            RecentRunSkippedItem.owner_id == settings.owner_id,
            RecentRunSkippedItem.run_key == run_key,
        )
        .order_by(RecentRunSkippedItem.created_at.desc(), RecentRunSkippedItem.id.desc())
    )
    if outcome:
        query = query.filter(RecentRunSkippedItem.outcome == outcome)
    rows = query.all()
    items = [_recent_run_item_response(row) for row in rows]
    visible, next_cursor, has_next = _paginate_items(items, cursor=cursor, limit=limit)
    return RecentRunItemListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.patch("/recruiter-opportunities/{opportunity_id}", response_model=RecruiterOpportunityResponse)
def patch_recruiter_opportunity(
    opportunity_id: int,
    payload: RecruiterOpportunityPatchRequest,
    db: Session = Depends(get_db),
) -> RecruiterOpportunityResponse:
    row = (
        db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == settings.owner_id, RecruiterOpportunity.id == opportunity_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if payload.status is not None:
        if payload.status not in OPPORTUNITY_STATUS_VALUES:
            raise HTTPException(status_code=422, detail="Invalid opportunity status")
        row.status = payload.status
    if payload.notes is not None:
        row.notes = payload.notes
    db.commit()
    db.refresh(row)
    recruiter = (
        db.query(RecruiterNumber)
        .filter(RecruiterNumber.owner_id == settings.owner_id, RecruiterNumber.id == row.recruiter_number_id)
        .first()
    )
    return _recruiter_opportunity_response(row, recruiter)


@app.delete("/recruiter-opportunities/{opportunity_id}", response_model=RecruiterOpportunityDeleteResponse)
def delete_recruiter_opportunity(
    opportunity_id: int,
    db: Session = Depends(get_db),
) -> RecruiterOpportunityDeleteResponse:
    row = (
        db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == settings.owner_id, RecruiterOpportunity.id == opportunity_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    recruiter_number_id = row.recruiter_number_id
    db.delete(row)
    db.flush()

    remaining_count = (
        db.query(func.count(RecruiterOpportunity.id))
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.recruiter_number_id == recruiter_number_id,
        )
        .scalar()
        or 0
    )

    recruiter_number_deleted = False
    if remaining_count == 0:
        recruiter = (
            db.query(RecruiterNumber)
            .filter(
                RecruiterNumber.owner_id == settings.owner_id,
                RecruiterNumber.id == recruiter_number_id,
            )
            .first()
        )
        if recruiter:
            db.delete(recruiter)
            recruiter_number_deleted = True

    db.commit()
    return RecruiterOpportunityDeleteResponse(
        id=opportunity_id,
        deleted=True,
        recruiter_number_deleted=recruiter_number_deleted,
    )


def _requirement_text_for_opportunity(db: Session, row: RecruiterOpportunity) -> str:
    parts: list[str] = []
    if row.source_email_id:
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == row.source_email_id)
            .first()
        )
        if email:
            parts.extend([email.subject or "", email.body or "", email.skills_text or ""])
    elif row.external_opportunity_id:
        external = (
            db.query(ExternalOpportunity)
            .filter(ExternalOpportunity.owner_id == settings.owner_id, ExternalOpportunity.id == row.external_opportunity_id)
            .first()
        )
        if external:
            parts.extend([external.role or "", external.skills_text or "", external.raw_body or ""])
    if not any(part.strip() for part in parts):
        parts.extend([row.job_title or "", row.email_subject or "", row.extracted_skills or "", row.evidence or ""])
    return "\n".join(part for part in parts if part and part.strip())


@app.post("/recruiter-opportunities/{opportunity_id}/generate-cold-call-script", response_model=RecruiterOpportunityResponse)
def generate_recruiter_opportunity_cold_call_script(
    opportunity_id: int,
    db: Session = Depends(get_db),
) -> RecruiterOpportunityResponse:
    row = (
        db.query(RecruiterOpportunity)
        .filter(RecruiterOpportunity.owner_id == settings.owner_id, RecruiterOpportunity.id == opportunity_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    resume = _active_resume(db)
    resume_text = ""
    if resume:
        try:
            resume_text = extract_resume_context(resume.file_path, resume.file_name)
        except Exception:
            resume_text = ""
    resume_skills_text = _resume_skills_text_for_cold_call(resume, resume_text)
    requirement_text = _requirement_text_for_opportunity(db, row)
    allowed_matches = find_allowed_cold_call_skills(
        requirement_text=requirement_text,
        resume_skills_text=resume_skills_text,
        resume_text=resume_text,
        max_skills=2,
    )
    recruiter = (
        db.query(RecruiterNumber)
        .filter(RecruiterNumber.owner_id == settings.owner_id, RecruiterNumber.id == row.recruiter_number_id)
        .first()
    )

    context = ColdCallContext(
        recruiter_name=(recruiter.recruiter_name if recruiter else ""),
        recruiter_email=((recruiter.recruiter_email if recruiter else "") or row.email_sender or ""),
        job_title=row.job_title or row.email_subject or "this role",
        location=row.location or "unknown",
        allowed_skill_highlights=", ".join(match.display for match in allowed_matches),
        allowed_skill_canonicals=tuple(match.canonical for match in allowed_matches),
        evidence=row.evidence or "",
    )
    row.cold_call_script = generate_cold_call_script(
        context=context,
        resume_text=resume_text,
        model_name=settings.deepseek_model_fast,
    )
    row.cold_call_script_updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(row)
    return _recruiter_opportunity_response(row, recruiter)


@app.get("/candidates/{email_id}", response_model=EmailResponse)
def get_candidate(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    return _get_candidate_review(email_id, db)


@app.get("/candidates/{email_id}/sent-details", response_model=SentItemDetailsResponse)
def get_sent_item_details(email_id: int, db: Session = Depends(get_db)) -> SentItemDetailsResponse:
    email = _get_candidate_for_review(db, email_id)
    if email.state != "approved_sent":
        raise HTTPException(status_code=400, detail="Sent item details are only available for approved_sent candidates")
    return _build_sent_item_details(db, email)


@app.post("/candidates/{email_id}/approve-send", response_model=EmailResponse)
def approve_and_send(
    email_id: int,
    payload: ApproveSendRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().approve_send(email_id, payload, db)


@app.post("/candidates/{email_id}/reject", response_model=EmailResponse)
def reject_candidate(
    email_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().reject_candidate(email_id, payload, db)


@app.post("/candidates/{email_id}/send-to-failed-mapping", response_model=EmailResponse)
def send_to_failed_mapping(email_id: int, db: Session = Depends(get_db)) -> RecruiterEmail:
    return _get_orchestration_service().send_to_failed_mapping(email_id, db)


@app.post("/candidates/{email_id}/regenerate", response_model=EmailResponse)
def regenerate_candidate(
    email_id: int,
    payload: RegenerateCandidateRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().regenerate_candidate(email_id, payload, db)


@app.delete("/candidates/{email_id}", response_model=dict[str, int | bool | str])
def dismiss_failed_candidate(email_id: int, db: Session = Depends(get_db)) -> dict[str, int | bool | str]:
    return _get_orchestration_service().dismiss_failed_candidate(email_id, db)


@app.post("/candidates/reject-bulk")
def reject_bulk(payload: BulkRejectRequest, db: Session = Depends(get_db)) -> dict[str, int]:
    if not payload.ids:
        return {"rejected_count": 0}

    rows = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id)
        .filter(RecruiterEmail.id.in_(payload.ids))
        .all()
    )
    rejected = 0
    for row in rows:
        if row.state == "needs_review":
            row.state = "rejected"
            row.decision = "Reject"
            row.decision_reason = payload.reason or "Bulk rejected by user"
            row.approval_status = "rejected"
            row.sent_status = "not_sent"
            rejected += 1
    db.commit()
    return {"rejected_count": rejected}


@app.post("/candidates/{email_id}/resolve-recipients", response_model=EmailResponse)
def resolve_recipients(
    email_id: int,
    payload: ResolveRecipientsRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().resolve_recipients(email_id, payload, db)
