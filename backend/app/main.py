import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path
from typing import Annotated, Mapping, TypedDict, TypeVar, cast
import threading
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import and_, func, not_, or_
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from rq import Retry
from rq.registry import StartedJobRegistry
from rq.command import send_stop_job_command
from rq.job import Job, JobStatus
from rq.exceptions import NoSuchJobError

from app.config import settings
from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.ai.draft_formatting import normalize_draft_text_size
from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_MISSING,
)
from app.ai.resume_context import extract_resume_context
from app.cold_call import ColdCallContext, find_allowed_cold_call_skills, generate_cold_call_script
from app.automation import (
    RunOrchestrator,
    RunOrchestratorDependencies,
    RunOrchestratorRequest,
)
from app.db import SessionLocal, get_db
from app.gates import classify_email_intent
from app.ai.groq_client import groq_request_mode_for_model
from app.gmail_client import (
    GmailMessageCandidate,
    get_message_thread_id,
    get_message_rfc_message_id,
    gmail_auth_status,
    get_candidates_by_message_ids,
    is_gmail_configured,
    list_thread_messages,
    list_unread_candidates_by_query,
    list_unread_thread_ids,
    MailAttachment,
    mark_message_processed,
    mark_reply_processed,
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
    APPLICATION_CLOSED_STATUS_VALUES,
    APPLICATION_STATUS_VALUES,
    APPLICATION_SUGGESTION_STATUS_VALUES,
    RESUME_SUBMISSION_STATUS_VALUES,
    Application,
    ApplicationEvent,
    ApplicationInterview,
    ApplicationOutreachMessage,
    ApplicationRTR,
    ApplicationSuggestion,
    ApplicationSkillGapSnapshot,
    AppTSApplication,
    AppTSApplicationEvent,
    AppTSApplicationInterview,
    AppTSApplicationRTR,
    AppTSApplicationSkillGapSnapshot,
    AttachmentAsset,
    BulkActionIdempotencyKey,
    CandidateDocument,
    CandidateRecord,
    CanonicalEntityTaxonomyEntry,
    CustomSkillTaxonomyEntry,
    DraftEditFeedback,
    EmailConversation,
    GmailRequirementGroup,
    JobIntentTaxonomyEntry,
    NumberReviewQueue,
    OpportunityLifecycleEvent,
    OpportunityLineage,
    PremiumNumberContact,
    PremiumContactEmail,
    PremiumContactPhone,
    PremiumNumberExtractionAudit,
    PremiumNumberLead,
    ProductivityEvent,
    RecentRun,
    RecentRunSkippedItem,
    RecruiterEmail,
    RecruiterOpportunity,
    ResumeAsset,
    SyncRun,
    UserSettings,
    utc_now,
)
from app.models import RecipientRoutingFeedback
from app.parsing.document_extraction import prepare_gmail_parse_body
from app.parsing.skill_audit import analyze_skill_candidate, is_safe_for_bulk_skill_approval
from app.telegram_bot import TelegramBotService, TelegramReply
from app.taxonomy.job_description_taxonomy import clear_job_intent_signal_embedding_cache
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
from app.recent_runs import (
    RUN_SOURCE_AUTOMATION,
    RUN_SOURCE_GMAIL_SYNC,
    RUN_SOURCE_MANUAL_INTAKE,
    RUN_SOURCE_NVOIDS_SYNC,
    automation_run_key,
    build_gmail_message_url,
    create_recent_run,
    gmail_sync_run_key,
    manual_intake_run_key,
    row_to_recent_run_dict,
    NVOIDS_CLIENT_SEARCH_PREFIX,
)
from app.jobs.queues import (
    AUTOMATION_RUN_QUEUE,
    EMBEDDING_QUEUE,
    GMAIL_SYNC_QUEUE,
    MANUAL_INTAKE_QUEUE,
    NVOIDS_SYNC_QUEUE,
    active_job_id,
    get_queue,
    get_redis_connection,
    redis_is_ready,
)
from app.jobs.tasks import (
    run_automation_job,
    run_generate_embedding_job,
    run_gmail_sync_job,
    run_manual_intake_job,
    run_nvoids_sync_job,
    run_retry_selected_messages_job,
)
from app.skill_taxonomy import (
    TAXONOMY_PLACEHOLDER_KEYS,
    clear_skill_taxonomy_cache,
    extract_skills_text,
    load_skill_taxonomy,
    normalize_skill_token,
    normalize_skills_text,
    normalize_taxonomy_text,
)
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES
from app.premium_numbers.domain_guard import (
    PERSONAL_EMAIL_DOMAINS,
    employer_domains_for_owner,
    is_derivable_company_domain,
    is_hidden_invalid_employer_number,
    is_hidden_nvoids_placeholder_recruiter,
)
from app.premium_numbers.phone_normalization import best_display_phone, canonicalize_phone, format_phone
from app.premium_numbers import contact_identity_service
from app.query_bucket import sanitize_saved_queries
from app.runtime_state import runtime_state
from app.routers.chat import (
    get_chat_service as _get_chat_service,
    require_chat_actions_enabled,
    router as chat_router,
)
from app.job_intent_learning import (
    NEGATIVE_NEWSLETTER,
    POSITIVE_RECRUITER_JD,
    approved_learning_signals_for_owner,
    normalize_job_intent_phrase,
    prioritized_learning_signals,
)
from app.services import (
    analytics_service,
    candidate_profile_service,
    entity_embedding_job,
    entity_resolution_service,
    relationship_clustering_service,
    relationship_judgment_service,
    relationship_labeling_service,
    application_intelligence_service,
    application_outreach_service,
    application_service,
    appts_service,
    resume_tracking_service,
    role_gap_service,
    why_this_resume_service,
    email_lookup_service,
    end_client_validation,
    filter_options_service,
    nvoids_search_job,
    opportunity_lineage_service,
    policy_service,
    recruiter_identity_service,
    role_similarity_service,
)
from app.services.scheduling import pending_work
from app.services.scheduling import schedule as scheduling_schedule
from app.services.scheduling import sweep as scheduling_sweep
from app.services.scheduling import task_service
from app.services.auto_runner_service import AutoRunnerService
from app.services.chat_attachment_service import ChatAttachmentService
from app.services.chat_provenance import user_supplied_document
from app.services.candidate_runtime_service import CandidateRuntimeDeps, CandidateRuntimeService
from app.services.manual_intake_service import (
    ManualIntakeDeps,
    ManualIntakeResult,
    ManualIntakeService,
    manual_intake_length_error,
)
from app.services.phone_intelligence_workflow_service import (
    apply_contact_version,
    capture_sister_company,
    derive_company_from_email_domain,
    derive_name_from_contact_email,
    job_metadata_ai_extraction_from_parsed,
)
from app.services.gmail_group_source_service import (
    canonical_group_display_name,
    normalize_google_group_email,
    normalize_google_group_slug,
    parse_group_inputs,
)
from app.services.gmail_labeling_runtime_service import GmailLabelingRuntimeService
from app.services.email_inbox_service import TRANSPARENT_PIXEL_PNG, record_open, reply_count_for_email
from app.services.github_issue_service import GithubIssueServiceError, create_github_issue
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService
from app.services.requirement_expansion_service import RequirementExpansionService
from app.services.resume_enrichment_service import backfill_role_and_label, enrich_resume
from app.services.role_manifest_pipeline import extract_and_score_children
from app.services.role_manifest_service import RoleManifestService
from app.services.sendability_service import SENDABILITY_BUCKETS, resolve_sendability_status
from app.services.routing_runtime_service import RoutingRuntimeDeps, RoutingRuntimeService
from app.services.scoring_runtime_service import ScoringRuntimeDeps, ScoringRuntimeService
from app.services.settings_bootstrap_service import SettingsBootstrapService
from app.services.startup_service import StartupService
from app.services.role_taxonomy import clear_role_taxonomy_cache
from app.services.taxonomy_bulk_review_service import (
    MAX_APPLY_KEYS,
    SKILL_SCOPE,
    BulkReviewCountMismatch,
    Recommendation,
    bucket_counts,
    classify_entities,
    classify_skills,
    refine_with_model,
    select_applicable,
)
from app.services.taxonomy_learning_service import (
    BULK_APPROVAL_MIN_OCCURRENCES,
    ENTITY_TYPES,
    embed_pending_skills,
    is_safe_for_bulk_entity_approval,
    list_pending_entities,
    upsert_entity,
)
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.schemas import (
    AIStatusResponse,
    ApplicationCreateRequest,
    ApplicationDashboardSummaryResponse,
    ApplicationDraftMessageRequest,
    ApplicationDraftMessageResponse,
    ApplicationEventCreateRequest,
    ApplicationEventResponse,
    ApplicationInterviewCreateRequest,
    ApplicationInterviewPatchRequest,
    ApplicationInterviewResponse,
    ApplicationListResponse,
    ApplicationOutreachMessageResponse,
    ApplicationPatchRequest,
    ApplicationResponse,
    ApplicationRTRRequest,
    ApplicationRTRResponse,
    ApplicationRTRUpdateRequest,
    ApplicationSendMessageRequest,
    ApplicationSendMessageResponse,
    ApplicationSubmitToClientRequest,
    ApplicationSuggestionListResponse,
    ApplicationSuggestionResolveRequest,
    ApplicationSuggestionResponse,
    ApplicationSkillGapResponse,
    ApproveJobIntentSignalRequest,
    ApproveSendRequest,
    ApproveSkillRequest,
    AttachmentAssetResponse,
    AttachmentAssetUpdateRequest,
    AutomationRunRequest,
    AutomationRunResponse,
    BulkNumberReviewRequest,
    BulkNumberReviewResponse,
    BulkNumberReviewResultItem,
    BulkContactActionRequest,
    BulkContactActionResponse,
    BulkContactActionResultItem,
    CandidateDocumentResponse,
    CandidateDocumentUpdateRequest,
    CandidateProfileResponse,
    ProfileAppendRequest,
    ProfileDeleteRequest,
    ProfileReplaceFromAttachmentRequest,
    ManualDuplicateSummary,
    ManualRequirementCreateRequest,
    ManualRequirementFromChatRequest,
    ManualRequirementPreviewRequest,
    ManualRequirementPreviewResponse,
    NvoidsClientSearchRequest,
    ContactFieldChange,
    ContactRescoreResponse,
    ContactMergePreviewLead,
    ContactMergePreviewResponse,
    ContactMergePreviewSide,
    ContactMergeRequest,
    ContactMergeResponse,
    DuplicateContactBackfillResponse,
    BulkApproveJobIntentSignalsResponse,
    BulkApproveSkillsResponse,
    BulkApproveEntitiesResponse,
    BulkReviewApplyRequest,
    BulkReviewApplyResponse,
    BulkReviewClassifyRequest,
    BulkReviewClassifyResponse,
    BulkReviewRecommendation,
    BulkApproveRequest,
    BulkCandidateActionResponse,
    BulkDeleteCandidatesRequest,
    BulkRegenerateRequest,
    BulkRejectRequest,
    BulkResolveRecipientsRequest,
    BulkSendToFailedMappingRequest,
    BulkTrackRequest,
    CandidateListResponse,
    ConversationDetailResponse,
    ConversationReplyRequest,
    ConversationSummaryResponse,
    ChatSendReplyRequest,
    GithubIssueCreateRequest,
    CustomSkillTaxonomyEntryResponse,
    CanonicalEntityTaxonomyEntryResponse,
    EntityAliasMergeRequest,
    RelationshipJudgmentRequest,
    ScheduledRunApproveRequest,
    ScheduledTaskCreateRequest,
    ScheduledTaskItemPatchRequest,
    ScheduledTaskPatchRequest,
    RelationshipLabelRequest,
    DismissJobIntentSignalRequest,
    DismissSkillRequest,
    DismissEntityRequest,
    EmbedPendingSkillsResponse,
    EmbeddedJobIntentSignalResponse,
    EmailSearchHitResponse,
    EmailSearchResponse,
    EmbeddingStatusResponse,
    EmailResponse,
    GmailStatusResponse,
    GmailSyncResponse,
    GmailLabelingPreviewRequest,
    GmailLabelingPreviewResponse,
    JobIntentTaxonomyEntryResponse,
    JobEnqueueResponse,
    JobQueueSummaryResponse,
    LiveReplyStatusResponse,
    JobStatusResponse,
    OAuthStartResponse,
    OAuthUrlResponse,
    EmployerNumberPatchRequest,
    EmployerNumberResponse,
    EmployerNumberListResponse,
    ExtractionAuditEntryResponse,
    ExtractionAuditListResponse,
    PremiumNumberListResponse,
    PremiumCompanyCardResponse,
    PremiumCompanyDetailResponse,
    PremiumCompanyListResponse,
    PremiumCompanyOpportunityResponse,
    PremiumCompanyPipelineResponse,
    PremiumCompanyResponsivenessResponse,
    TrackedCount,
    PremiumNumberInventoryItemResponse,
    PremiumNumberInventoryListResponse,
    PremiumNumberResponse,
    ManualPremiumContactRequest,
    ManualApplicationCreateRequest,
    PendingNumberReviewCountResponse,
    PendingSkillResponse,
    PendingEntityResponse,
    ApproveEntityRequest,
    RecruiterNumberResponse,
    RecruiterReputationResponse,
    RecruiterNumberListResponse,
    RecruiterNumberPatchRequest,
    RecruiterOpportunityDeleteResponse,
    RecruiterOpportunityListResponse,
    RecruiterOpportunityPatchRequest,
    RecruiterOpportunityResponse,
    OpportunityMatchListResponse,
    OpportunityMatchResponse,
    NumberReviewSubmitRequest,
    ContactMergeApprovalRequest,
    RejectRequest,
    ResolveRecipientsRequest,
    ResumeResponse,
    ResumeFunnelMetricsResponse,
    ResumePerformanceSummaryItem,
    ResumePerformanceSummaryResponse,
    ResumeSubmissionStatusUpdateRequest,
    ResumeUpdateRequest,
    RoleGapReportResponse,
    VariantLookupResponse,
    WhyThisResumeResponse,
    parse_variant_token,
    resume_variant_code,
    resume_variant_token,
    SentItemDetailsResponse,
    SettingsBootstrapResponse,
    SettingsRequest,
    SettingsResponse,
    VisibleFiltersRequest,
    UnknownNumberReviewCardListResponse,
    UnknownNumberReviewCardResponse,
    ProductivityEventCreateRequest,
    ProductivityEventResponse,
    ProductivityBarPoint,
    ProductivityTrendResponse,
    RecordDetailResponse,
    RecentRunItemListResponse,
    RecentRunItemResponse,
    RecentRunListResponse,
    RecentRunResponse,
    RecentRunSkippedItemRetryRequest,
    RegenerateCandidateRequest,
    RoleDetectionRetryResponse,
    TelegramStatusResponse,
    TaxonomyMetricsResponse,
    ExternalFeedSyncResponse,
    ExternalScrapeRunResponse,
    FilterOptionsResponse,
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
    generate_embeddings,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global auto_runner_thread, telegram_service, gmail_labeling_service
    async with AsyncExitStack() as stack:
        if settings.feature_chat_enabled:
            await stack.enter_async_context(chat_mcp.session_manager.run())
            runtime_state.chat_mcp_status = "ready"
        startup_service = StartupService(
            ensure_default_settings=_ensure_default_settings,
            ensure_labeling_service=gmail_labeling_runtime_service.ensure_service,
            init_telegram_service=_init_telegram_service,
            auto_runner_loop=_auto_runner_loop,
        )
        startup_service.startup()
        auto_runner_thread = runtime_state.auto_runner_thread
        telegram_service = runtime_state.telegram_service
        gmail_labeling_service = runtime_state.gmail_labeling_service
        yield
        startup_service.shutdown()
        auto_runner_thread = runtime_state.auto_runner_thread
        telegram_service = runtime_state.telegram_service
        gmail_labeling_service = runtime_state.gmail_labeling_service


app = FastAPI(title=settings.app_name, lifespan=lifespan)
if settings.feature_chat_enabled:
    from app.mcp_server.server import mcp as chat_mcp, mcp_app as chat_mcp_app
else:
    chat_mcp = None
app.include_router(chat_router)
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
manual_intake_service: ManualIntakeService | None = None
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
app.add_middleware(GZipMiddleware, minimum_size=1000)

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
    "view_manual_intake": 0.1,
    "view_premium_numbers": 0.1,
    "view_assistant": 0.1,
}

ALLOWED_VIEW_EVENTS = {
    "view_needs_review",
    "view_failed_mapping",
    "view_recent_runs",
    "view_sent_items",
    "view_run_queue",
    "view_manual_intake",
    "view_premium_numbers",
    "view_assistant",
}

RANGE_OPTIONS = {"last_1h", "current_day", "current_week", "current_month", "current_year", "last_5y"}
BUSINESS_TZ = ZoneInfo("America/Chicago")
BUCKET_OPTIONS = {"five_min", "hour", "day", "month", "quarter"}


def _record_productivity_event(
    db: Session,
    *,
    event_type: str,
    event_source: str,
    entity_id: int | None = None,
    entity_type: str = "",
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
        entity_type=entity_type,
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


def _mail_date_utc_window(selected: date) -> tuple[datetime, datetime]:
    start_local = datetime(selected.year, selected.month, selected.day, tzinfo=BUSINESS_TZ)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _enqueue_embedding_generation(*, record_type: str, record_id: int) -> None:
    try:
        get_queue(EMBEDDING_QUEUE).enqueue(
            run_generate_embedding_job,
            kwargs={"record_type": record_type, "record_id": record_id},
            retry=Retry(max=3, interval=[10, 30, 90]),
            job_timeout=60,
            result_ttl=3600,
            failure_ttl=86400,
        )
    except Exception:
        logger.warning("embedding_enqueue_failed record_type=%s record_id=%s", record_type, record_id, exc_info=True)


def _date_range_utc_window(
    preset: str,
    custom_from: date | None = None,
    custom_to: date | None = None,
) -> tuple[datetime, datetime]:
    today = datetime.now(BUSINESS_TZ).date()
    if preset == "today":
        start, end = today, today
    elif preset == "yesterday":
        start = end = today - timedelta(days=1)
    elif preset == "last_7_days":
        start, end = today - timedelta(days=6), today
    elif preset == "custom" and custom_from and custom_to and custom_from <= custom_to:
        start, end = custom_from, custom_to
    else:
        raise HTTPException(status_code=422, detail="Invalid date filter")
    start_utc, _ = _mail_date_utc_window(start)
    _, end_utc = _mail_date_utc_window(end)
    return start_utc, end_utc


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


def _json_string_list_map(value: str | None) -> dict[str, list[str]]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key): [str(item) for item in items if isinstance(item, str)]
        for key, items in parsed.items()
        if isinstance(items, list)
    }


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


def _load_recruiter_number_for_sent_details(
    db: Session,
    email: RecruiterEmail,
    recruiter_email: str | None,
) -> PremiumNumberContact | None:
    if email.resolved_recruiter_contact_id:
        row = (
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == settings.owner_id,
                PremiumNumberContact.id == email.resolved_recruiter_contact_id,
                PremiumNumberContact.is_recruiter.is_(True),
                PremiumNumberContact.deleted_at.is_(None),
            )
            .first()
        )
        if row:
            return row
    row = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.first_detected_email_id == email.id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if row:
        return row
    if not recruiter_email:
        return None
    return (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.is_recruiter.is_(True),
            or_(
                PremiumNumberContact.recruiter_email == recruiter_email,
                PremiumNumberContact.id.in_(
                    db.query(PremiumContactEmail.premium_contact_id).filter(
                        PremiumContactEmail.owner_id == settings.owner_id,
                        PremiumContactEmail.normalized_email == recruiter_email.strip().lower(),
                    )
                ),
            ),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc())
        .first()
    )


def _resolve_recruiter_contact_for_email(
    db: Session,
    email: RecruiterEmail,
    *,
    create_missing: bool = True,
) -> tuple[str | None, PremiumNumberContact | None]:
    normalized, sender_name = recruiter_identity_service.stamp_recruiter_email_identity(db, email)
    contact = _load_recruiter_number_for_sent_details(db, email, normalized)
    if contact is None and normalized and create_missing:
        result = contact_identity_service.reconcile(
            db,
            owner_id=email.owner_id,
            normalized_email=normalized,
            name=sender_name or "",
            company=(email.company or "").strip() or "Unknown",
            role="recruiter",
            source_email_id=email.id,
            human_confirmed=False,
        )
        contact = result.contact
    if create_missing:
        email.resolved_recruiter_contact_id = contact.id if contact else None
    return normalized, contact


def _load_employer_number_for_sent_details(
    db: Session,
    email: RecruiterEmail,
    employer_email: str | None,
) -> PremiumNumberContact | None:
    row = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.is_employer.is_(True),
            PremiumNumberContact.first_detected_email_id == email.id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if row:
        return row
    if not employer_email:
        return None
    return (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.is_employer.is_(True),
            PremiumNumberContact.employer_email == employer_email,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc())
        .first()
    )


def _load_premium_lead_for_sent_details(
    db: Session, email: RecruiterEmail, *, role: str | None = None
) -> PremiumNumberLead | None:
    query = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.recruiter_email_id == email.id,
    )
    if role is not None:
        query = query.filter(PremiumNumberLead.role == role)
    return query.order_by(
        PremiumNumberLead.is_recruiter_relevant.desc(),
        PremiumNumberLead.recruiter_relevance_score.desc(),
        PremiumNumberLead.id.desc(),
    ).first()


_NVOIDS_MESSAGE_ID_PATTERN = re.compile(r"^nvoids:(?:nvoids:)?(\d+)$", re.IGNORECASE)


def _sent_item_requirement_link(email: RecruiterEmail, external: ExternalOpportunity | None) -> str | None:
    if email.source == "nvoids":
        candidate = _clean_optional_text((external.source_url if external else None) or email.external_thread_id)
        if candidate and candidate.lower().startswith(("http://", "https://")):
            return candidate
        message_id = _clean_optional_text(email.external_message_id)
        match = _NVOIDS_MESSAGE_ID_PATTERN.match(message_id) if message_id else None
        if match:
            return f"https://nvoids.com/job_details.jsp?id={match.group(1)}"
        return candidate
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
    recruiter_premium_lead = _load_premium_lead_for_sent_details(db, email, role="recruiter")

    employer_domains = employer_domains_for_owner(db, email.owner_id)

    def _domain_matched_address(address: str | None, *, want_employer_domain: bool) -> str | None:
        cleaned = _clean_optional_text(address)
        if not cleaned:
            return None
        is_employer = email_domain(cleaned) in employer_domains
        return cleaned if is_employer == want_employer_domain else None

    recipient_clean = _clean_optional_text(email.recipient_email)
    recruiter_email, recruiter_number = _resolve_recruiter_contact_for_email(db, email)
    employer_email_guess = (
        _domain_matched_address(recipient_clean, want_employer_domain=True)
        or _domain_matched_address(sender_email, want_employer_domain=True)
    )
    employer_number = _load_employer_number_for_sent_details(db, email, employer_email_guess)
    company = (
        _clean_optional_text(external.company if external else None)
        or _clean_optional_text(recruiter_opportunity.end_client if recruiter_opportunity else None)
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
        resume_variant_code=resume_variant_code(email.resume_asset_id) or None,
        resume_variant_token=resume_variant_token(email.resume_asset_id, email.id) or None,
        attached_files=_json_string_list(email.sent_attachment_file_names_json),
        company=company,
        recruiter_name=(
            _clean_optional_text(external.recruiter_name if external else None)
            or _clean_optional_text(recruiter_number.recruiter_name if recruiter_number else None)
            or _clean_optional_text(recruiter_premium_lead.owner_name if recruiter_premium_lead else None)
            or (
                sender_name
                if sender_email and recruiter_email and sender_email.lower() == recruiter_email.lower()
                else None
            )
        ),
        recruiter_email=recruiter_email,
        recruiter_email_domain=(_clean_optional_text(email_domain(recruiter_email)) if recruiter_email else None),
        recruiter_phone=(
            _clean_optional_text(external.recruiter_phone if external else None)
            or _clean_optional_text(recruiter_number.display_phone_number if recruiter_number else None)
            or _clean_optional_text(recruiter_premium_lead.phone_number_display if recruiter_premium_lead else None)
        ),
        recruiter_company=(
            _clean_optional_text(recruiter_number.company if recruiter_number else None)
            or _clean_optional_text(recruiter_premium_lead.company if recruiter_premium_lead else None)
        ),
        employer_name=_clean_optional_text(employer_number.owner_name if employer_number else None),
        employer_email=(
            _clean_optional_text(employer_number.employer_email if employer_number else None)
            or employer_email_guess
        ),
        employer_email_domain=(_clean_optional_text(email_domain(employer_email_guess)) if employer_email_guess else None),
        employer_phone=_clean_optional_text(employer_number.display_phone_number if employer_number else None),
        employer_company=_clean_optional_text(employer_number.company if employer_number else None),
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
        opened_at=email.opened_at,
        open_count=int(email.open_count or 0),
        reply_count=reply_count_for_email(db, email.owner_id, email.id),
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


def _enqueue_background_job(
    db: Session,
    *,
    queue_name: str,
    run_source: str,
    run_key: str,
    task: Callable[..., object],
    task_kwargs: dict[str, object],
    total_items: int | None = None,
    sync_batch_id: str | None = None,
) -> JobEnqueueResponse:
    try:
        current_job_id = active_job_id(queue_name)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"job_queue_unavailable: {exc}") from exc
    if current_job_id:
        existing_row = (
            db.query(RecentRun)
            .filter(RecentRun.owner_id == settings.owner_id, RecentRun.job_backend_id == current_job_id)
            .first()
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "another_job_in_progress",
                "job_id": current_job_id,
                "run_key": existing_row.run_key if existing_row else None,
            },
        )

    job_id = uuid.uuid4().hex
    recent_run = create_recent_run(
        db,
        owner_id=settings.owner_id,
        run_source=run_source,
        run_key=run_key,
        status="queued",
        detail=f"Queued on {queue_name}.",
        job_backend_id=job_id,
        total_items=total_items,
        processed_items=0,
        progress_pct=0.0,
        queue_name=queue_name,
        sync_batch_id=sync_batch_id,
    )
    db.commit()
    try:
        queue = get_queue(queue_name)
        queue.enqueue(
            task,
            kwargs=task_kwargs,
            job_id=job_id,
            retry=Retry(max=2, interval=[15, 60]),
            job_timeout=1800,
            result_ttl=86400,
            failure_ttl=604800,
        )
    except Exception as exc:
        recent_run.status = "failed"
        recent_run.detail = f"Failed to enqueue background job: {exc}"
        db.commit()
        raise HTTPException(status_code=503, detail=f"job_enqueue_failed: {exc}") from exc
    return JobEnqueueResponse(run_key=run_key, job_id=job_id, status="queued")


def _get_manual_intake_service() -> ManualIntakeService:
    global manual_intake_service
    if manual_intake_service is None:
        manual_intake_service = ManualIntakeService(
            ManualIntakeDeps(
                owner_id=settings.owner_id,
                model_name=settings.deepseek_model_fast,
                get_settings=_get_settings,
                active_resume=_active_resume,
                enabled_resumes=_enabled_resumes,
                evaluate_routing_policy=lambda db, sender, subject, body, snippet="", routing_confirmed=False, precomputed=None: _get_routing_runtime_service().evaluate_routing_policy(
                    db, sender, subject, body, snippet, routing_confirmed, precomputed=precomputed
                ),
                apply_routing_decision=lambda email, routing: _get_routing_runtime_service().apply_routing_decision(email, routing),
                capture_premium_numbers=_capture_premium_numbers,
            )
        )
    return manual_intake_service


def _manual_intake_text(text: str) -> str:
    """The pasted requirement, refused here if it is empty or over the cap.

    One reader for `settings.manual_intake_max_chars`, which until now had none:
    the number lived as literals in two request models and the dashboard, so the
    setting could be changed with no effect while the three literals drifted.

    The detail is a sentence rather than a code because it is shown to whoever
    pasted the text, and "20,431 characters" is the only part of it they can act
    on. A paste this long is a document - it belongs on the upload path, not in
    a textarea.
    """
    error = manual_intake_length_error(text)
    if error:
        raise HTTPException(status_code=400, detail=error)
    return text or ""


def _run_manual_intake(db: Session, *, text: str) -> ManualIntakeResult:
    """Called by the worker, never by a request handler - ingestion is queued."""
    return _get_manual_intake_service().ingest(db, text=text)


def _enqueue_manual_intake(db: Session, *, text: str) -> JobEnqueueResponse:
    run_key = manual_intake_run_key(uuid.uuid4().hex)
    return _enqueue_background_job(
        db,
        queue_name=MANUAL_INTAKE_QUEUE,
        run_source=RUN_SOURCE_MANUAL_INTAKE,
        run_key=run_key,
        task=run_manual_intake_job,
        task_kwargs={"run_key": run_key, "text": text},
        total_items=1,
    )


def _enqueue_gmail_sync(db: Session) -> JobEnqueueResponse:
    sync_batch_id = str(uuid.uuid4())
    run_key = gmail_sync_run_key(sync_batch_id)
    return _enqueue_background_job(
        db,
        queue_name=GMAIL_SYNC_QUEUE,
        run_source=RUN_SOURCE_GMAIL_SYNC,
        run_key=run_key,
        task=run_gmail_sync_job,
        task_kwargs={"run_key": run_key, "sync_batch_id": sync_batch_id},
        sync_batch_id=sync_batch_id,
    )


def _enqueue_nvoids_sync(db: Session, *, max_items: int) -> JobEnqueueResponse:
    run_key = f"nvoids_sync:job-{uuid.uuid4().hex}"
    return _enqueue_background_job(
        db,
        queue_name=NVOIDS_SYNC_QUEUE,
        run_source=RUN_SOURCE_NVOIDS_SYNC,
        run_key=run_key,
        task=run_nvoids_sync_job,
        task_kwargs={"run_key": run_key, "max_items": max_items},
        total_items=max_items,
    )


def _enqueue_nvoids_client_search(db: Session, *, criteria: dict) -> JobEnqueueResponse:
    """One nvoids search against supplied criteria, queued like any other job.

    Its own run-key prefix so the assistant can find the run it started rather
    than the most recent scheduled sync, which may be someone else's.
    """
    run_key = f"{NVOIDS_CLIENT_SEARCH_PREFIX}{uuid.uuid4().hex}"
    max_items = int(criteria.get("batch_limit") or 10)
    return _enqueue_background_job(
        db,
        queue_name=NVOIDS_SYNC_QUEUE,
        run_source=RUN_SOURCE_NVOIDS_SYNC,
        run_key=run_key,
        task=run_nvoids_sync_job,
        task_kwargs={"run_key": run_key, "max_items": max_items, "criteria": criteria},
        total_items=max_items,
    )


def _enqueue_automation(payload: AutomationRunRequest | None, db: Session) -> JobEnqueueResponse:
    run_key = automation_run_key(uuid.uuid4().hex)
    return _enqueue_background_job(
        db,
        queue_name=AUTOMATION_RUN_QUEUE,
        run_source=RUN_SOURCE_AUTOMATION,
        run_key=run_key,
        task=run_automation_job,
        task_kwargs={"run_key": run_key, "payload": payload.model_dump() if payload else None},
        total_items=1,
    )


def _enqueue_retry_selected_skipped_items(db: Session, external_message_ids: list[str]) -> JobEnqueueResponse:
    run_key = automation_run_key(uuid.uuid4().hex)
    return _enqueue_background_job(
        db,
        queue_name=AUTOMATION_RUN_QUEUE,
        run_source=RUN_SOURCE_AUTOMATION,
        run_key=run_key,
        task=run_retry_selected_messages_job,
        task_kwargs={"run_key": run_key, "external_message_ids": external_message_ids},
        total_items=len(external_message_ids),
    )


def _check_live_replies(db: Session) -> None:
    _, authenticated, _ = gmail_auth_status()
    if not authenticated:
        return
    runtime_state.live_reply_count = _get_orchestration_service().count_live_unread_replies(db)
    runtime_state.live_reply_checked_at = datetime.now(UTC)


def _run_reminder_sweep(db: Session) -> None:
    user_settings = _get_settings(db)
    application_intelligence_service.generate_reminder_sweep_suggestions(
        db,
        owner_id=user_settings.owner_id,
    )
    db.commit()


def _run_resume_tracking_sweep(db: Session) -> None:
    user_settings = _get_settings(db)
    resume_tracking_service.generate_resume_tracking_suggestions(
        db,
        owner_id=user_settings.owner_id,
    )
    db.commit()


def _run_relationship_sweep(db: Session) -> None:
    """Top up entity embeddings, then run one clustering pass.

    Both halves are idempotent and resumable, and the clustering pass writes
    shadow rows unless surfacing is explicitly enabled *and* the thresholds have
    been calibrated. `since` is deliberately left open: the pass is bounded by
    `max_pairs` and the blocking keys rather than by a watermark, so a record
    whose neighbours arrive later is still reconsidered.
    """
    entity_embedding_job.embed_pending_entities_all_types(db, owner_id=settings.owner_id)
    result = relationship_clustering_service.run_clustering_pass(db, owner_id=settings.owner_id)
    logger.info(
        "Relationship sweep: scored=%s clusters=%s suppressed=%s surfaced=%s",
        result.scored_pairs,
        result.clusters_written,
        result.clusters_suppressed,
        result.surfaced,
    )


def _get_auto_runner_service() -> AutoRunnerService:
    global auto_runner_service
    if auto_runner_service is None:
        auto_runner_service = AutoRunnerService(
            session_factory=SessionLocal,
            get_settings=_get_settings,
            run_once=_enqueue_automation,
            run_nvoids_once=lambda db, max_items: _enqueue_nvoids_sync(db, max_items=max_items),
            check_live_replies=_check_live_replies,
            run_reminder_sweep=_run_reminder_sweep,
            run_resume_tracking_sweep=_run_resume_tracking_sweep,
            run_relationship_sweep=_run_relationship_sweep,
            run_scheduling_sweep=_run_scheduling_sweep,
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
                get_preferred_employer_cc_emails=lambda db: _preferred_employer_cc_emails(_get_settings(db)),
                get_default_employer_cc_emails=lambda db: _csv_to_list(_get_settings(db).default_employer_cc_emails),
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
                hard_filter_check=lambda parsed, user_settings, effective_policy, parser_details=None: hard_filter_check(parsed, user_settings, effective_policy, parser_details),
                greeting_from_to_contact=lambda to_email, body: greeting_from_to_contact(to_email, body),
                generate_reply_with_ai_or_fallback=lambda **kwargs: generate_reply_with_ai_or_fallback(**kwargs),
                send_reply_with_attachment=lambda *args, **kwargs: send_reply_with_attachment(*args, **kwargs),
                send_new_email_with_attachment=lambda *args, **kwargs: send_new_email_with_attachment(*args, **kwargs),
                mark_message_processed=lambda message_id: mark_message_processed(message_id),
                append_tracking_sheet_row=lambda **kwargs: append_tracking_sheet_row(**kwargs),
                mark_reply_processed=lambda message_id, label_ids=None: mark_reply_processed(message_id, label_ids),
                get_message_thread_id=lambda message_id: get_message_thread_id(message_id),
                get_message_rfc_message_id=lambda message_id: get_message_rfc_message_id(message_id),
                list_thread_messages=lambda thread_id: list_thread_messages(thread_id),
                list_unread_thread_ids=lambda: list_unread_thread_ids(),
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


def _preferred_employer_cc_emails(user_settings: UserSettings) -> list[str]:
    configured = _csv_to_list(user_settings.preferred_employer_cc_emails)
    if configured:
        return configured
    legacy = (user_settings.preferred_employer_cc_email or "").strip().lower()
    return [legacy] if legacy else []


def _policy_f2f_block(parsed: dict[str, str | int | bool], policy: PolicyConfig, user_settings: UserSettings) -> tuple[bool, str]:
    normalized = policy_service.normalize_policy(policy)
    qualification = normalized["qualification"]
    strictness = policy_service.as_str(qualification.get("location_strictness", "balanced"), "balanced")
    accepted_rule = qualification["draft_rules"]["accepted_location"]
    accepted_locations = [loc.strip().lower() for loc in accepted_rule.get("locations", []) if loc.strip()] or [
        loc.strip().lower() for loc in user_settings.accepted_locations.split(",") if loc.strip()
    ]
    f2f_blocked, f2f_reason = should_block_f2f(parsed, accepted_locations)
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


def _resume_response(resume: ResumeAsset) -> ResumeResponse:
    return ResumeResponse.model_validate(resume).model_copy(
        update={"structured_skills": _json_string_list(resume.structured_skills_json)}
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
            _vector, payload, _provider, _chunks = _get_scoring_runtime_service()._safe_embed_with_chunking(
                None, resume_text
            )
            resume.semantic_embedding = payload
        else:
            resume.semantic_embedding = None
    except Exception as exc:
        logger.warning("Resume semantic embedding skipped: %s", exc)


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


def _list_candidate_documents(db: Session) -> list[CandidateDocument]:
    return (
        db.query(CandidateDocument)
        .filter(CandidateDocument.owner_id == settings.owner_id)
        .order_by(CandidateDocument.created_at.desc(), CandidateDocument.id.desc())
        .all()
    )


def _get_candidate_document(db: Session, document_id: int) -> CandidateDocument:
    document = (
        db.query(CandidateDocument)
        .filter(CandidateDocument.owner_id == settings.owner_id, CandidateDocument.id == document_id)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _resolve_candidate_documents(db: Session, document_ids: list[int]) -> list[CandidateDocument]:
    """Turn requested ids into documents, in the order asked, or refuse.

    A partial send is the wrong failure here: the user confirmed a card listing
    three files, and a mail that quietly leaves one out is worse than one that
    is not sent. So an unknown id, a missing file on disk, or a batch over
    Gmail's ceiling stops the whole send.
    """
    if not document_ids:
        return []
    wanted = list(dict.fromkeys(int(value) for value in document_ids))
    found = {
        row.id: row
        for row in db.query(CandidateDocument).filter(
            CandidateDocument.owner_id == settings.owner_id,
            CandidateDocument.id.in_(wanted),
        )
    }
    missing = [value for value in wanted if value not in found]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown document ids: {', '.join(str(value) for value in missing)}",
        )
    documents = [found[value] for value in wanted]
    absent = [item.file_name for item in documents if not Path(item.file_path).exists()]
    if absent:
        raise HTTPException(
            status_code=409,
            detail=f"Document file is missing from disk: {', '.join(absent)}. Re-upload it in Settings.",
        )
    total = sum(item.file_size for item in documents)
    if total > settings.candidate_document_max_send_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Attachments total {total // (1024 * 1024)}MB, over the "
                f"{settings.candidate_document_max_send_bytes // (1024 * 1024)}MB a single mail can carry"
            ),
        )
    return documents


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


def _serialize_canonical_entity(entry: CanonicalEntityTaxonomyEntry) -> CanonicalEntityTaxonomyEntryResponse:
    payload = CanonicalEntityTaxonomyEntryResponse.model_validate(entry).model_dump()
    try:
        aliases = json.loads(entry.aliases_json or "[]")
    except json.JSONDecodeError:
        aliases = []
    payload["aliases"] = [str(item).strip() for item in aliases if str(item).strip()]
    return CanonicalEntityTaxonomyEntryResponse.model_validate(payload)


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
        unknown_source = "legacy"
        if skills_json:
            try:
                skills_payload = json.loads(skills_json)
            except json.JSONDecodeError:
                skills_payload = None
            if isinstance(skills_payload, dict):
                raw_unknown = skills_payload.get("unknown", [])
                if isinstance(raw_unknown, list):
                    unknown_skills = raw_unknown
                    stored_source = str(skills_payload.get("unknown_source") or "legacy")
                    unknown_source = stored_source if stored_source in {"ai", "base", "legacy"} else "legacy"
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
            analysis = analyze_skill_candidate(skill_name)
            if (
                not skill_name
                or not normalized
                or normalized in TAXONOMY_PLACEHOLDER_KEYS
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
                    "suspicious": False,
                    "recoverable_skills": set(),
                    "source_tags": set(),
                },
            )
            bucket["occurrence_count"] = int(bucket["occurrence_count"]) + 1
            bucket["suspicious"] = bool(bucket["suspicious"]) or analysis.suspicious or bool(analysis.recovered_skills)
            candidate_ids = cast(list[int], bucket["candidate_ids"])
            candidate_ids.append(int(email_id))
            recoverable = cast(set[str], bucket["recoverable_skills"])
            recoverable.update(analysis.recovered_skills)
            source_tags = cast(set[str], bucket["source_tags"])
            source_tags.add(unknown_source)
    results: list[PendingSkillResponse] = []
    for item in aggregated.values():
        candidate_ids = sorted(set(cast(list[int], item["candidate_ids"])), reverse=True)
        results.append(
            PendingSkillResponse(
                skill_name=str(item["skill_name"]),
                normalized_name=str(item["normalized_name"]),
                occurrence_count=int(item["occurrence_count"]),
                candidate_ids=candidate_ids,
                suspicious=bool(item["suspicious"]),
                recoverable_skills=sorted(cast(set[str], item["recoverable_skills"]), key=str.casefold),
                source_tags=sorted(cast(set[str], item["source_tags"])),
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
    occurrence_count: int = 0,
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
        row.occurrence_count = max(int(row.occurrence_count or 0), max(0, occurrence_count))
        if status == "approved":
            row.embedding_status = "pending"
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
        occurrence_count=max(0, occurrence_count),
        embedding_status="pending",
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
        clear_job_intent_signal_embedding_cache()
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
    clear_job_intent_signal_embedding_cache()
    return created


def _semantic_text_for_resume(resume: ResumeAsset | None) -> str:
    return _get_scoring_runtime_service().semantic_text_for_resume(resume)


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


def _apply_routing_result(email: RecruiterEmail, routing: RoutingResult) -> None:
    _get_routing_runtime_service().apply_routing_result(email, routing)


def _apply_routing_decision(email: RecruiterEmail, routing: RoutingDecision) -> None:
    _get_routing_runtime_service().apply_routing_decision(email, routing)


def _capture_premium_numbers(db: Session, email: RecruiterEmail) -> None:
    _get_candidate_runtime_service().capture_premium_numbers(db, email)


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
    queued_email_ids: list[int] | None = None,
) -> AutomationRunResponse:
    if not email:
        return AutomationRunResponse(
            status=status,
            detail=detail,
            run_key=run_key,
            queued_email_ids=queued_email_ids or [],
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
        queued_email_ids=queued_email_ids or [],
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
        gate_error=row.gate_error,
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
    preferred_employer_cc_emails = _preferred_employer_cc_emails(s)
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
        nvoids_job_role=s.nvoids_job_role or "",
        nvoids_search_location=s.nvoids_search_location or "",
        nvoids_custom_query=s.nvoids_custom_query or "",
        nvoids_end_client=s.nvoids_end_client or "",
        nvoids_query_mode=s.nvoids_query_mode or "composed",
        feature_auto_send=s.feature_auto_send,
        feature_retry_queue=s.feature_retry_queue,
        feature_ai_enabled=s.feature_ai_enabled,
        feature_ai_extractor_enabled=s.feature_ai_extractor_enabled,
        feature_semantic_enabled=s.feature_semantic_enabled,
        feature_groq_job_parser_enabled=s.feature_groq_job_parser_enabled,
        feature_gmail_requirement_groups_enabled=s.feature_gmail_requirement_groups_enabled,
        feature_role_manifest_enabled=s.feature_role_manifest_enabled,
        feature_strict_candidate_screening_enabled=s.feature_strict_candidate_screening_enabled,
        feature_email_tracking_enabled=s.feature_email_tracking_enabled,
        feature_reply_inbox_enabled=s.feature_reply_inbox_enabled,
        feature_applications_enabled=s.feature_applications_enabled,
        feature_application_automation_enabled=s.feature_application_automation_enabled,
        feature_application_outreach_drafts_enabled=s.feature_application_outreach_drafts_enabled,
        feature_reminder_sweep_interval_minutes=max(
            30,
            min(int(s.feature_reminder_sweep_interval_minutes or 240), 1440),
        ),
        feature_resume_tracking_enabled=s.feature_resume_tracking_enabled,
        feature_resume_variant_marker_enabled=s.feature_resume_variant_marker_enabled,
        feature_resume_tracking_sweep_interval_minutes=max(
            30,
            min(int(s.feature_resume_tracking_sweep_interval_minutes or 240), 1440),
        ),
        candidate_work_authorizations=_json_string_list(s.candidate_work_authorizations_json),
        preferred_employment_types=_json_string_list(s.preferred_employment_types_json),
        visible_filters=_json_string_list_map(s.visible_filters_json),
        preferred_minimum_rate=s.preferred_minimum_rate,
        candidate_total_experience_years=s.candidate_total_experience_years,
        candidate_us_experience_years=s.candidate_us_experience_years,
        candidate_current_location=s.candidate_current_location or "",
        candidate_profile_markdown=s.candidate_profile_markdown or "",
        candidate_profile_filename=s.candidate_profile_filename or "",
        candidate_profile_uploaded_at=s.candidate_profile_uploaded_at,
        draft_text_size=normalize_draft_text_size(s.draft_text_size),
        fallback_draft_template=s.fallback_draft_template or DEFAULT_FALLBACK_DRAFT_TEMPLATE,
        signature_name=(s.signature_name or "").strip() or DEFAULT_SIGNATURE_NAME,
        signature_phone=(s.signature_phone or "").strip() or DEFAULT_SIGNATURE_PHONE,
        signature_email=(s.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL,
        preferred_employer_cc_emails=preferred_employer_cc_emails,
        default_employer_cc_emails=_csv_to_list(s.default_employer_cc_emails),
        preferred_employer_cc_email=preferred_employer_cc_emails[0] if preferred_employer_cc_emails else "",
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


def _compact_resume_picker_candidates(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    compact: dict[str, object] = {}
    selected = value.get("selected_resume_file_name")
    if isinstance(selected, str):
        compact["selected_resume_file_name"] = selected
    rankings = value.get("rankings")
    if isinstance(rankings, list):
        ranking_fields = (
            "resume_file_name",
            "final_resume_score",
            "ai_score",
            "ats_score",
            "selection_reason",
        )
        compact["rankings"] = [
            {field: ranking[field] for field in ranking_fields if field in ranking}
            for ranking in rankings
            if isinstance(ranking, Mapping)
        ]
    return compact


def _serialize_candidate_for_review(db: Session, email: RecruiterEmail) -> EmailResponse:
    _hydrate_candidates_for_review(db, [email])
    _resolve_recruiter_contact_for_email(db, email)
    badge_fields = _populate_badge_fields(db, email.owner_id, [email])
    if db.new or db.dirty:
        db.commit()
        db.refresh(email)
    payload = EmailResponse.model_validate(email).model_dump()
    payload.update(badge_fields.get(email.id, {}))
    payload["attachment_file_names"] = _enabled_attachment_file_names(db)
    payload["parser_details"] = email.parser_details_json
    payload["sendability_status"] = resolve_sendability_status(email)
    return EmailResponse.model_validate(payload)


def _identity_key(row: RecruiterEmail | AppTSApplication) -> str | None:
    if row.resolved_recruiter_contact_id:
        return f"contact:{row.resolved_recruiter_contact_id}"
    return f"email:{row.resolved_recruiter_email}" if row.resolved_recruiter_email else None


def _contact_categories(contact: PremiumNumberContact) -> list[str]:
    return [name for name, flag in (("Recruiter", contact.is_recruiter), ("Employer", contact.is_employer)) if flag]


def _contact_status(contact: PremiumNumberContact) -> str:
    unknown = lambda value: not str(value or "").strip() or str(value).strip().lower() == "unknown"
    flagged = (
        (contact.is_recruiter and (unknown(contact.recruiter_name) or unknown(contact.company)))
        or (contact.is_employer and (unknown(contact.owner_name) or unknown(contact.company)))
        or not contact.phone_is_valid
    )
    return "Flagged" if flagged else "Active"


def _recruiter_email_guess_for_badges(
    db: Session,
    email: RecruiterEmail,
    employer_domains: set[str],
) -> str | None:
    _sender_name, sender_email = _parse_sender_contact(email.sender)
    external = _load_external_opportunity_for_sent_details(db, email) if email.source == "nvoids" else None

    def matched(address: str | None, *, employer: bool) -> str | None:
        cleaned = _clean_optional_text(address)
        return cleaned if cleaned and (email_domain(cleaned) in employer_domains) == employer else None

    recruiter_email = (
        _clean_optional_text(external.recruiter_email if external else None)
        or matched(email.recipient_email, employer=False)
        or matched(sender_email, employer=False)
    )
    return recruiter_email.strip().lower() if recruiter_email else None


def _existing_recruiter_contacts_for_badges(
    db: Session,
    owner_id: str,
    emails: list[RecruiterEmail],
) -> tuple[dict[int, str | None], dict[int, PremiumNumberContact | None]]:
    ids = [email.id for email in emails]
    employer_domains = employer_domains_for_owner(db, owner_id)
    normalized_by_email_id = {
        email.id: (_recruiter_email_guess_for_badges(db, email, employer_domains) or email.resolved_recruiter_email)
        for email in emails
    }
    by_resolved_id: dict[int, PremiumNumberContact] = {}
    resolved_ids = {email.resolved_recruiter_contact_id for email in emails if email.resolved_recruiter_contact_id}
    if resolved_ids:
        for contact in db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.id.in_(resolved_ids), PremiumNumberContact.is_recruiter.is_(True), PremiumNumberContact.deleted_at.is_(None)).all():
            by_resolved_id[contact.id] = contact

    by_first_email_id: dict[int, PremiumNumberContact] = {}
    if ids:
        for contact in db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.first_detected_email_id.in_(ids), PremiumNumberContact.is_recruiter.is_(True), PremiumNumberContact.deleted_at.is_(None)).order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc()).all():
            if contact.first_detected_email_id is not None:
                by_first_email_id.setdefault(contact.first_detected_email_id, contact)

    by_normalized_email: dict[str, PremiumNumberContact] = {}
    normalized_values = {value for value in normalized_by_email_id.values() if value}
    if normalized_values:
        for contact in db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id == owner_id, PremiumNumberContact.is_recruiter.is_(True), func.lower(PremiumNumberContact.recruiter_email).in_(normalized_values), PremiumNumberContact.deleted_at.is_(None)).order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc()).all():
            key = (contact.recruiter_email or "").strip().lower()
            if key:
                by_normalized_email.setdefault(key, contact)
        for normalized_email, contact in (
            db.query(PremiumContactEmail.normalized_email, PremiumNumberContact)
            .join(PremiumNumberContact, PremiumNumberContact.id == PremiumContactEmail.premium_contact_id)
            .filter(
                PremiumContactEmail.owner_id == owner_id,
                PremiumContactEmail.normalized_email.in_(normalized_values),
                PremiumNumberContact.owner_id == owner_id,
                PremiumNumberContact.is_recruiter.is_(True),
                PremiumNumberContact.deleted_at.is_(None),
            )
            .order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc())
            .all()
        ):
            by_normalized_email.setdefault(normalized_email, contact)

    contact_by_email_id: dict[int, PremiumNumberContact | None] = {}
    for email in emails:
        normalized = normalized_by_email_id.get(email.id)
        contact = by_first_email_id.get(email.id)
        if contact is None and email.resolved_recruiter_contact_id:
            contact = by_resolved_id.get(email.resolved_recruiter_contact_id)
        if contact is None and normalized:
            contact = by_normalized_email.get(normalized)
        contact_by_email_id[email.id] = contact
    return normalized_by_email_id, contact_by_email_id


def _populate_badge_fields(
    db: Session,
    owner_id: str,
    emails: list[RecruiterEmail],
) -> dict[int, dict[str, object]]:
    normalized_by_email_id, resolved = _existing_recruiter_contacts_for_badges(db, owner_id, emails)
    identity_keys: dict[int, str | None] = {}
    for email in emails:
        contact = resolved[email.id]
        normalized = normalized_by_email_id.get(email.id)
        identity_keys[email.id] = f"contact:{contact.id}" if contact else (_identity_key(email) or (f"email:{normalized}" if normalized else None))
    keys = set(identity_keys.values()) - {None}
    if not keys:
        return {email.id: {"marked_for_tracking": bool(email.marked_for_tracking)} for email in emails}
    contact_ids = {int(key.split(":", 1)[1]) for key in keys if key.startswith("contact:")}
    bare_emails = {key.split(":", 1)[1] for key in keys if key.startswith("email:")}

    def identity_clause(model: type):
        clauses = []
        if contact_ids:
            clauses.append(model.resolved_recruiter_contact_id.in_(contact_ids))
        if bare_emails:
            clauses.append(and_(model.resolved_recruiter_contact_id.is_(None), model.resolved_recruiter_email.in_(bare_emails)))
        return or_(*clauses)

    bookmarked = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.state == "needs_review", RecruiterEmail.marked_for_tracking.is_(True), identity_clause(RecruiterEmail)).order_by(RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc()).all()
    tracked = db.query(AppTSApplication).filter(AppTSApplication.owner_id == owner_id, AppTSApplication.deleted_at.is_(None), identity_clause(AppTSApplication)).order_by(AppTSApplication.created_at.desc(), AppTSApplication.id.desc()).all()
    active = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == owner_id, RecruiterEmail.state == "approved_sent", identity_clause(RecruiterEmail)).order_by(RecruiterEmail.sent_at.desc(), RecruiterEmail.id.desc()).all()

    grouped: dict[str, dict[str, list[object]]] = {}
    for kind, rows in (("bookmarked", bookmarked), ("tracked", tracked), ("active", active)):
        for row in rows:
            key = _identity_key(row)
            if key:
                grouped.setdefault(key, {"bookmarked": [], "tracked": [], "active": []})[kind].append(row)
    result: dict[int, dict[str, object]] = {}
    for email in emails:
        contact = resolved[email.id]
        values: dict[str, object] = {"marked_for_tracking": bool(email.marked_for_tracking)}
        if contact and contact.normalized_phone_number:
            values.update(premium_status=_contact_status(contact), premium_verification_level=contact.recruiter_verification_level)
        matches = grouped.get(identity_keys.get(email.id) or "", {})
        selected_kind = next((kind for kind in ("tracked", "bookmarked", "active") if any(getattr(row, "id", None) != email.id or kind == "tracked" for row in matches.get(kind, []))), None)
        if selected_kind:
            match = next(row for row in matches[selected_kind] if getattr(row, "id", None) != email.id or selected_kind == "tracked")
            match_role = match.job_title_snapshot if selected_kind == "tracked" else match.role
            match_skills = match.resume_skills_snapshot_json if selected_kind == "tracked" else match.skills_text
            similarity = role_similarity_service.compute_role_similarity(
                db, owner_id=owner_id, left_type="recruiter_email", left_id=email.id,
                left_role_text=email.role or "", left_skills_text=email.skills_text or "",
                right_type="appts_application" if selected_kind == "tracked" else "recruiter_email", right_id=match.id,
                right_role_text=match_role or "", right_skills_text=match_skills or "",
                left_embedding_cached=email.semantic_embedding,
                right_embedding_cached=match.embedding if selected_kind == "tracked" else match.semantic_embedding,
            )
            values["following_badge"] = selected_kind
            values["following_warning"] = (
                "This recruiter has a previous requirement that may be similar — review before proceeding."
                if similarity.tier == "related"
                else f"This recruiter has a prior {selected_kind} requirement for {'a similar' if similarity.tier == 'same' else 'a different'} role ({match_role or 'Not specified'})."
            )
        result[email.id] = values
    if db.new:
        db.commit()
    return result


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


@app.get("/track/open/{token}.png")
def track_email_open(token: str, request: Request, db: Session = Depends(get_db)) -> Response:
    try:
        record_open(
            db,
            token=token,
            user_agent=request.headers.get("user-agent", ""),
            remote_ip=request.client.host if request.client else "",
        )
    except Exception:
        db.rollback()
        logger.exception("email_open_tracking_failed")
    return Response(
        content=TRANSPARENT_PIXEL_PNG,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    return _settings_response_from_model(s)


@app.get("/settings/bootstrap", response_model=SettingsBootstrapResponse)
def get_settings_bootstrap(
    include_learning_data: bool = Query(True),
    db: Session = Depends(get_db),
) -> SettingsBootstrapResponse:
    user_settings = _get_settings(db)
    pending_skills = _list_pending_unknown_skills(db) if include_learning_data else []
    pending_job_intent_signals = (
        [_serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="pending")]
        if include_learning_data
        else []
    )
    approved_job_intent_signals = (
        [_serialize_job_intent_entry(item) for item in _list_job_intent_entries(db, status="approved")]
        if include_learning_data
        else []
    )
    return SettingsBootstrapResponse(
        settings=_settings_response_from_model(user_settings),
        role_manifest_child_creation_enabled=settings.role_manifest_child_creation_enabled,
        scheduling_enabled=settings.feature_scheduling_enabled,
        gmail_requirement_groups=[_gmail_requirement_group_response(item) for item in _list_gmail_requirement_groups(db)],
        resumes=[_resume_response(item) for item in _list_resumes(db)],
        attachments=[AttachmentAssetResponse.model_validate(item) for item in _list_attachment_assets(db)],
        documents=[CandidateDocumentResponse.model_validate(item) for item in _list_candidate_documents(db)],
        pending_skills=pending_skills,
        pending_job_intent_signals=pending_job_intent_signals,
        approved_job_intent_signals=approved_job_intent_signals,
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
    s.nvoids_job_role = payload.nvoids_job_role
    s.nvoids_search_location = payload.nvoids_search_location
    s.nvoids_custom_query = payload.nvoids_custom_query
    s.nvoids_end_client = payload.nvoids_end_client
    s.nvoids_query_mode = payload.nvoids_query_mode
    s.feature_auto_send = payload.feature_auto_send
    s.feature_retry_queue = payload.feature_retry_queue
    s.feature_ai_enabled = payload.feature_ai_enabled
    s.feature_ai_extractor_enabled = payload.feature_ai_extractor_enabled
    s.feature_semantic_enabled = payload.feature_semantic_enabled
    s.feature_groq_job_parser_enabled = payload.feature_groq_job_parser_enabled
    s.feature_gmail_requirement_groups_enabled = payload.feature_gmail_requirement_groups_enabled
    s.feature_email_tracking_enabled = payload.feature_email_tracking_enabled
    s.feature_reply_inbox_enabled = payload.feature_reply_inbox_enabled
    s.feature_applications_enabled = payload.feature_applications_enabled
    s.feature_application_automation_enabled = payload.feature_application_automation_enabled
    s.feature_application_outreach_drafts_enabled = payload.feature_application_outreach_drafts_enabled
    s.feature_reminder_sweep_interval_minutes = max(
        30,
        min(int(payload.feature_reminder_sweep_interval_minutes), 1440),
    )
    s.feature_resume_tracking_enabled = payload.feature_resume_tracking_enabled
    s.feature_resume_variant_marker_enabled = payload.feature_resume_variant_marker_enabled
    s.feature_resume_tracking_sweep_interval_minutes = max(
        30,
        min(int(payload.feature_resume_tracking_sweep_interval_minutes), 1440),
    )
    provided_fields = payload.model_fields_set
    if "feature_role_manifest_enabled" in provided_fields:
        s.feature_role_manifest_enabled = payload.feature_role_manifest_enabled
    if "feature_strict_candidate_screening_enabled" in provided_fields:
        s.feature_strict_candidate_screening_enabled = payload.feature_strict_candidate_screening_enabled
    if "candidate_work_authorizations" in provided_fields:
        s.candidate_work_authorizations_json = json.dumps(payload.candidate_work_authorizations or [], separators=(",", ":"))
    if "preferred_employment_types" in provided_fields:
        s.preferred_employment_types_json = json.dumps(payload.preferred_employment_types, separators=(",", ":"))
    if "visible_filters" in provided_fields:
        s.visible_filters_json = json.dumps(payload.visible_filters, separators=(",", ":"))
    if "preferred_minimum_rate" in provided_fields:
        s.preferred_minimum_rate = payload.preferred_minimum_rate
    if "candidate_total_experience_years" in provided_fields:
        s.candidate_total_experience_years = payload.candidate_total_experience_years
    if "candidate_us_experience_years" in provided_fields:
        s.candidate_us_experience_years = payload.candidate_us_experience_years
    if "candidate_current_location" in provided_fields:
        s.candidate_current_location = (payload.candidate_current_location or "").strip()
    # candidate_profile_markdown is deliberately absent here: it is written only
    # by POST/DELETE /settings/candidate-profile, and is response-only on the
    # schema so a settings save can neither set nor blank it.
    s.draft_text_size = normalize_draft_text_size(payload.draft_text_size)
    s.fallback_draft_template = payload.fallback_draft_template.strip() if payload.fallback_draft_template.strip() else DEFAULT_FALLBACK_DRAFT_TEMPLATE
    s.signature_name = payload.signature_name.strip() if payload.signature_name.strip() else DEFAULT_SIGNATURE_NAME
    s.signature_phone = payload.signature_phone.strip() if payload.signature_phone.strip() else DEFAULT_SIGNATURE_PHONE
    s.signature_email = payload.signature_email.strip() if payload.signature_email.strip() else DEFAULT_SIGNATURE_EMAIL
    if "preferred_employer_cc_emails" in provided_fields:
        preferred_employer_cc_emails = payload.preferred_employer_cc_emails
    elif "preferred_employer_cc_email" in provided_fields:
        preferred_employer_cc_emails = [payload.preferred_employer_cc_email] if payload.preferred_employer_cc_email else []
    else:
        preferred_employer_cc_emails = _preferred_employer_cc_emails(s)
    s.preferred_employer_cc_emails = _to_csv(preferred_employer_cc_emails)
    s.preferred_employer_cc_email = preferred_employer_cc_emails[0] if preferred_employer_cc_emails else ""
    if "default_employer_cc_emails" in provided_fields:
        s.default_employer_cc_emails = _to_csv(payload.default_employer_cc_emails)
    s.resume_display_name = payload.resume_display_name.strip()
    normalized_policy = policy_service.normalize_policy(
        payload.policy if payload.policy is not None else policy_service.read_policy_from_settings(s.policy_json)
    )
    s.policy_json = json.dumps(normalized_policy, separators=(",", ":"))
    db.commit()
    db.refresh(s)
    return _settings_response_from_model(s)


@app.put("/settings/visible-filters", response_model=SettingsResponse)
def update_visible_filters(
    payload: VisibleFiltersRequest,
    db: Session = Depends(get_db),
) -> SettingsResponse:
    s = _get_settings(db)
    s.visible_filters_json = json.dumps(payload.visible_filters, separators=(",", ":"))
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


# Re-exported from the service so there is one definition of each limit rather
# than two that can drift. Routes and tests still read them from `main`.
CANDIDATE_PROFILE_MAX_CHARS = candidate_profile_service.CANDIDATE_PROFILE_MAX_CHARS
CANDIDATE_PROFILE_MAX_BYTES = candidate_profile_service.CANDIDATE_PROFILE_MAX_BYTES
CANDIDATE_PROFILE_SUFFIXES = candidate_profile_service.CANDIDATE_PROFILE_SUFFIXES


def _candidate_profile_response(s: UserSettings) -> CandidateProfileResponse:
    return CandidateProfileResponse(
        filename=s.candidate_profile_filename or "",
        uploaded_at=s.candidate_profile_uploaded_at,
        characters=len(s.candidate_profile_markdown or ""),
    )


@app.post("/settings/candidate-profile", response_model=CandidateProfileResponse)
def upload_candidate_profile(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> CandidateProfileResponse:
    """Replace the stored profile with the text of an uploaded Markdown file.

    The text is stored, not the file. Every rejection below names the actual
    value that failed, because the alternative is a user re-uploading the same
    document repeatedly against a message that does not say what is wrong.
    """
    filename = candidate_profile_service.validate_profile_filename(file.filename or "")
    # Read bounded: an arbitrarily large upload would otherwise be decoded in
    # full only to be rejected for length a moment later.
    raw = file.file.read(candidate_profile_service.CANDIDATE_PROFILE_MAX_BYTES + 1)
    text = candidate_profile_service.validate_profile_text(raw, filename)

    s = settings_bootstrap_service.get_settings(db)
    s.candidate_profile_markdown = text
    s.candidate_profile_filename = filename[:255]
    s.candidate_profile_uploaded_at = datetime.now(UTC)
    db.commit()
    db.refresh(s)
    return _candidate_profile_response(s)


def _profile_fingerprint_or_409(current: str, base_sha256: str | None) -> None:
    """R7. A card computed against one document, written against another, is a
    conflict the user has to see - never a silent overwrite."""
    if base_sha256 is None:
        return
    if candidate_profile_service.fingerprint(current) != base_sha256:
        raise HTTPException(
            status_code=409,
            detail=(
                "The profile changed since this was prepared. Ask again so the card "
                "shows what is actually stored."
            ),
        )


@app.post("/settings/candidate-profile/entries", response_model=CandidateProfileResponse)
def append_candidate_profile_entry(
    payload: ProfileAppendRequest,
    db: Session = Depends(get_db),
) -> CandidateProfileResponse:
    """Add one composed line to the profile.

    This one route serves all three paths: the assistant's offer, a user-directed
    save, and the Add-to-profile control in Settings. The body is identical in
    every case and the route neither knows nor cares which sent it - every
    provenance rule was enforced before the entry became an entry, and the
    fingerprint and empty-profile checks below apply to all three alike.

    Deliberately no `source` parameter: a route that branches on who called it is
    a route with two behaviours to keep in agreement.
    """
    s = settings_bootstrap_service.get_settings(db)
    current = s.candidate_profile_markdown or ""
    _profile_fingerprint_or_409(current, payload.base_sha256)
    if not current.strip():
        # R8. Appending to nothing is creating, and creating a profile is an
        # upload.
        raise HTTPException(
            status_code=400,
            detail=(
                "There is no profile to add to yet. Upload one in Settings › Profile "
                "Settings › Candidate Profile first."
            ),
        )

    combined = candidate_profile_service.compose_append(current, payload.entry)
    s.candidate_profile_markdown = combined
    # The filename still names the document this is an addition to, so it stays.
    s.candidate_profile_uploaded_at = datetime.now(UTC)
    db.commit()
    db.refresh(s)
    return _candidate_profile_response(s)


@app.post("/settings/candidate-profile/from-attachment", response_model=CandidateProfileResponse)
def replace_candidate_profile_from_attachment(
    payload: ProfileReplaceFromAttachmentRequest,
    db: Session = Depends(get_db),
) -> CandidateProfileResponse:
    """Replace the profile with the text of a file attached to a chat message.

    The caller supplies an id and the server re-reads the row, the same rule
    propose_send_email follows for documents: ids, never the text the card
    displayed.
    """
    row = ChatAttachmentService.get(db, payload.attachment_id)
    if row.content_markdown is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{row.file_name} could not be read: {row.extraction_error or 'extraction_error'}. "
                "Upload it in Settings instead."
            ),
        )
    # Extraction truncates at exactly the profile's own character limit, so a
    # too-long document arrives *at* the limit and passes validation. The user
    # would be told the replace worked and would lose the tail of their profile.
    if len(row.content_markdown) >= settings.chat_attachment_max_extract_chars:
        raise HTTPException(
            status_code=400,
            detail=(
                "That file was too long to read in full, so replacing the profile from "
                "it would lose the end of it. Upload it in Settings instead."
            ),
        )

    text = candidate_profile_service.validate_profile_text(row.content_markdown, row.file_name)
    s = settings_bootstrap_service.get_settings(db)
    _profile_fingerprint_or_409(s.candidate_profile_markdown or "", payload.base_sha256)
    s.candidate_profile_markdown = text
    s.candidate_profile_filename = row.file_name[:255]
    s.candidate_profile_uploaded_at = datetime.now(UTC)
    db.commit()
    db.refresh(s)
    return _candidate_profile_response(s)


@app.delete("/settings/candidate-profile", response_model=CandidateProfileResponse)
def delete_candidate_profile(
    payload: ProfileDeleteRequest | None = None,
    db: Session = Depends(get_db),
) -> CandidateProfileResponse:
    s = settings_bootstrap_service.get_settings(db)
    # A bodyless DELETE - which is what the Settings panel sends - behaves
    # exactly as it did before. A card supplies the fingerprint it computed
    # against, and a stale one is a 409.
    _profile_fingerprint_or_409(
        s.candidate_profile_markdown or "", payload.base_sha256 if payload else None
    )
    s.candidate_profile_markdown = ""
    s.candidate_profile_filename = ""
    s.candidate_profile_uploaded_at = None
    db.commit()
    db.refresh(s)
    return _candidate_profile_response(s)


@app.post("/settings/resume", response_model=ResumeResponse)
def upload_resume(
    file: UploadFile = File(...),
    skills_text: str = Form(""),
    primary_role: str = Form(""),
    structured_skills_text: str = Form(""),
    variant_label: str = Form(""),
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
        primary_role=primary_role.strip(),
        structured_skills_json=json.dumps(
            [value for value in _normalize_resume_skills_text(structured_skills_text).split(",") if value],
            separators=(",", ":"),
        ),
        variant_label=variant_label.strip(),
        is_enabled=True,
        is_current=True,
    )
    try:
        enrich_resume(resume)
    except Exception as exc:
        logger.warning("Resume enrichment skipped: %s", exc)
    _refresh_resume_embedding(resume)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return _resume_response(resume)


@app.get("/settings/resumes", response_model=list[ResumeResponse])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeResponse]:
    return [_resume_response(item) for item in _list_resumes(db)]


@app.post("/settings/resumes/backfill-enrichment")
def backfill_resume_enrichment(force: bool = False, db: Session = Depends(get_db)) -> dict[str, object]:
    query = db.query(ResumeAsset).filter(ResumeAsset.owner_id == settings.owner_id)
    if not force:
        query = query.filter(ResumeAsset.content_markdown.is_(None))
    rows = query.order_by(ResumeAsset.id).all()

    enriched_ids: list[int] = []
    failed: list[dict[str, object]] = []
    for resume in rows:
        try:
            enrich_resume(resume)
            _refresh_resume_embedding(resume)
            db.commit()
            enriched_ids.append(resume.id)
        except Exception as exc:
            db.rollback()
            logger.warning("Resume enrichment backfill failed for %s: %s", resume.id, exc)
            failed.append({"id": resume.id, "error": str(exc)[:500]})

    labeled_ids: list[int] = []
    if not force:
        label_rows = (
            db.query(ResumeAsset)
            .filter(
                ResumeAsset.owner_id == settings.owner_id,
                ResumeAsset.content_markdown.is_not(None),
                or_(ResumeAsset.primary_role == "", ResumeAsset.variant_label == ""),
            )
            .order_by(ResumeAsset.id)
            .all()
        )
        for resume in label_rows:
            if backfill_role_and_label(resume):
                labeled_ids.append(resume.id)
    if labeled_ids:
        db.commit()

    return {
        "enriched_count": len(enriched_ids),
        "enriched_ids": enriched_ids,
        "failed": failed,
        "labeled_count": len(labeled_ids),
        "labeled_ids": labeled_ids,
    }


@app.patch("/settings/resumes/{resume_id}", response_model=ResumeResponse)
def update_resume(resume_id: int, payload: ResumeUpdateRequest, db: Session = Depends(get_db)) -> ResumeResponse:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    if not payload.model_fields_set:
        raise HTTPException(status_code=400, detail="At least one resume update field is required")

    if payload.skills_text is not None:
        resume.skills_text = _normalize_resume_skills_text(payload.skills_text)
        _refresh_resume_embedding(resume)

    if payload.primary_role is not None:
        resume.primary_role = payload.primary_role.strip()
    if payload.structured_skills is not None:
        resume.structured_skills_json = json.dumps(
            list(dict.fromkeys(value.strip() for value in payload.structured_skills if value.strip())),
            separators=(",", ":"),
        )
    if payload.variant_label is not None:
        resume.variant_label = payload.variant_label.strip()

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
    return _resume_response(resume)


@app.delete("/settings/resumes/{resume_id}")
def delete_resume(resume_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    active_application = any(
        db.query(model.id)
        .filter(
            model.owner_id == settings.owner_id,
            model.resume_asset_id == resume_id,
            model.deleted_at.is_(None),
            model.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
        )
        .first()
        for model in (Application, AppTSApplication)
    )
    if active_application:
        raise HTTPException(
            status_code=409,
            detail="Resume is used by an active application; close or delete those applications first",
        )

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


@app.post("/settings/documents", response_model=list[CandidateDocumentResponse])
def upload_candidate_documents(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> list[CandidateDocumentResponse]:
    """Store documents the assistant can attach to a mail when asked by name.

    Any format is accepted: these are forwarded to the recruiter byte for byte
    and are never parsed, so there is nothing here that a content type could
    make safe or unsafe. What is enforced is size, because a file too large to
    send is better refused now than after a draft is written.
    """
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    max_bytes = settings.candidate_document_max_bytes
    Path(settings.candidate_document_storage_dir).mkdir(parents=True, exist_ok=True)
    staged: list[tuple[bytes, str, str, str]] = []
    for file in files:
        if not file.filename:
            raise HTTPException(status_code=400, detail="File name required")
        # One byte past the limit is enough to reject it, and stops a huge
        # upload being held in memory in full just to be refused.
        content = file.file.read(max_bytes + 1)
        if not content:
            raise HTTPException(status_code=400, detail=f"Empty file not allowed: {file.filename}")
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{file.filename} is larger than the "
                    f"{max_bytes // (1024 * 1024)}MB limit for a single document"
                ),
            )
        staged.append(
            (
                content,
                Path(file.filename).name[:255],
                file.content_type or "application/octet-stream",
                hashlib.sha256(content).hexdigest(),
            )
        )

    # Nothing is written until every file has passed, so one oversized file in a
    # multi-file pick does not leave half the batch stored.
    created: list[CandidateDocument] = []
    for content, file_name, mime_type, sha256 in staged:
        target_path = Path(settings.candidate_document_storage_dir) / f"{sha256}_{uuid.uuid4().hex}_{file_name}"
        target_path.write_bytes(content)
        created.append(
            CandidateDocument(
                owner_id=settings.owner_id,
                file_path=str(target_path),
                file_name=file_name,
                label="",
                mime_type=mime_type,
                sha256=sha256,
                file_size=len(content),
            )
        )
    db.add_all(created)
    db.commit()
    for item in created:
        db.refresh(item)
    return [CandidateDocumentResponse.model_validate(item) for item in created]


@app.get("/settings/documents", response_model=list[CandidateDocumentResponse])
def list_candidate_document_files(db: Session = Depends(get_db)) -> list[CandidateDocumentResponse]:
    return [CandidateDocumentResponse.model_validate(item) for item in _list_candidate_documents(db)]


@app.patch("/settings/documents/{document_id}", response_model=CandidateDocumentResponse)
def update_candidate_document(
    document_id: int,
    payload: CandidateDocumentUpdateRequest,
    db: Session = Depends(get_db),
) -> CandidateDocumentResponse:
    document = _get_candidate_document(db, document_id)
    document.label = " ".join(payload.label.split())[:120]
    db.commit()
    db.refresh(document)
    return CandidateDocumentResponse.model_validate(document)


@app.delete("/settings/documents/{document_id}")
def delete_candidate_document(document_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    document = _get_candidate_document(db, document_id)
    file_path = Path(document.file_path)
    db.delete(document)
    db.commit()
    try:
        if file_path.exists():
            file_path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Document deleted from database but not disk: {exc}") from exc
    return {"id": document_id, "deleted": True}


@app.get("/settings/skills/pending", response_model=list[PendingSkillResponse])
def list_pending_skills(db: Session = Depends(get_db)) -> list[PendingSkillResponse]:
    return _list_pending_unknown_skills(db)


@app.get("/settings/skills/approved", response_model=list[CustomSkillTaxonomyEntryResponse])
def list_approved_skills(db: Session = Depends(get_db)) -> list[CustomSkillTaxonomyEntryResponse]:
    return [_serialize_custom_skill_entry(item) for item in _list_approved_custom_skill_entries(db)]


@app.post("/settings/skills/approve", response_model=CustomSkillTaxonomyEntryResponse)
def approve_skill(payload: ApproveSkillRequest, db: Session = Depends(get_db)) -> CustomSkillTaxonomyEntryResponse:
    pending = {item.normalized_name: item for item in _list_pending_unknown_skills(db)}
    pending_item = pending.get(normalize_taxonomy_text(payload.skill_name))
    entry = _upsert_custom_skill_entry(
        db,
        skill_name=payload.skill_name,
        canonical_name=payload.canonical_name,
        aliases=payload.aliases,
        category=payload.category,
        cluster_hint=payload.cluster_hint,
        occurrence_count=pending_item.occurrence_count if pending_item else 0,
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
        if (
            not normalized
            or normalized in suppressed
            or item.occurrence_count < BULK_APPROVAL_MIN_OCCURRENCES
            or not is_safe_for_bulk_skill_approval(item.skill_name)
        ):
            continue
        _upsert_custom_skill_entry(
            db,
            skill_name=item.skill_name,
            canonical_name=item.skill_name,
            aliases=[],
            category="custom",
            cluster_hint=None,
            occurrence_count=item.occurrence_count,
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


@app.get("/settings/skills/embedding-status", response_model=EmbeddingStatusResponse)
def skill_embedding_status(db: Session = Depends(get_db)) -> EmbeddingStatusResponse:
    pending_count = (
        db.query(CustomSkillTaxonomyEntry)
        .filter(
            CustomSkillTaxonomyEntry.owner_id == settings.owner_id,
            CustomSkillTaxonomyEntry.status == "approved",
            CustomSkillTaxonomyEntry.embedding_status == "pending",
        )
        .count()
    )
    return EmbeddingStatusResponse(pending_count=pending_count)


@app.post("/settings/skills/embed-pending", response_model=EmbedPendingSkillsResponse)
def embed_approved_skills(db: Session = Depends(get_db)) -> EmbedPendingSkillsResponse:
    if not runtime_state.taxonomy_embedding_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A taxonomy embedding batch is already running")
    try:
        return EmbedPendingSkillsResponse.model_validate(
            embed_pending_skills(db, owner_id=settings.owner_id)
        )
    finally:
        runtime_state.taxonomy_embedding_lock.release()


@app.get("/settings/entities/{entity_type}/pending", response_model=list[PendingEntityResponse])
def list_pending_taxonomy_entities(
    entity_type: str,
    db: Session = Depends(get_db),
) -> list[PendingEntityResponse]:
    try:
        rows = list_pending_entities(db, owner_id=settings.owner_id, entity_type=entity_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [PendingEntityResponse.model_validate(row) for row in rows]


@app.get("/settings/entities/{entity_type}/approved", response_model=list[CanonicalEntityTaxonomyEntryResponse])
def list_approved_taxonomy_entities(
    entity_type: str,
    db: Session = Depends(get_db),
) -> list[CanonicalEntityTaxonomyEntryResponse]:
    if entity_type not in ENTITY_TYPES:
        raise HTTPException(status_code=404, detail="entity_type must be company or location")
    rows = (
        db.query(CanonicalEntityTaxonomyEntry)
        .filter(
            CanonicalEntityTaxonomyEntry.owner_id == settings.owner_id,
            CanonicalEntityTaxonomyEntry.entity_type == entity_type,
            CanonicalEntityTaxonomyEntry.status == "approved",
        )
        .order_by(CanonicalEntityTaxonomyEntry.canonical_name.asc())
        .all()
    )
    return [_serialize_canonical_entity(row) for row in rows]


@app.post("/settings/entities/{entity_type}/approve", response_model=CanonicalEntityTaxonomyEntryResponse)
def approve_taxonomy_entity(
    entity_type: str,
    payload: ApproveEntityRequest,
    db: Session = Depends(get_db),
) -> CanonicalEntityTaxonomyEntryResponse:
    try:
        pending = {
            str(item["normalized_name"]): item
            for item in list_pending_entities(db, owner_id=settings.owner_id, entity_type=entity_type)
        }
        item = pending.get(normalize_taxonomy_text(payload.display_name), {})
        row = upsert_entity(
            db,
            owner_id=settings.owner_id,
            entity_type=entity_type,
            display_name=payload.display_name,
            canonical_name=payload.canonical_name,
            aliases=payload.aliases,
            occurrence_count=int(item.get("occurrence_count", 0)),
            status="approved",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Approved/dismissed entries are what load_role_taxonomy reads, so the cached
    # vocabulary must drop the moment the human changes it.
    clear_role_taxonomy_cache()
    return _serialize_canonical_entity(row)


@app.post("/settings/entities/{entity_type}/approve-all", response_model=BulkApproveEntitiesResponse)
def approve_all_taxonomy_entities(
    entity_type: str,
    db: Session = Depends(get_db),
) -> BulkApproveEntitiesResponse:
    try:
        pending = list_pending_entities(db, owner_id=settings.owner_id, entity_type=entity_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    approved: list[str] = []
    for item in pending:
        if (
            int(item["occurrence_count"]) < BULK_APPROVAL_MIN_OCCURRENCES
            or not is_safe_for_bulk_entity_approval(str(item["display_name"]))
        ):
            continue
        upsert_entity(
            db,
            owner_id=settings.owner_id,
            entity_type=entity_type,
            display_name=str(item["display_name"]),
            canonical_name=None,
            aliases=[],
            occurrence_count=int(item["occurrence_count"]),
            status="approved",
            auto_commit=False,
        )
        approved.append(str(item["display_name"]))
    if approved:
        db.commit()
        clear_role_taxonomy_cache()
    return BulkApproveEntitiesResponse(
        processed_count=len(pending),
        approved_count=len(approved),
        skipped_count=len(pending) - len(approved),
        approved_names=approved,
    )


@app.post("/settings/entities/{entity_type}/dismiss", response_model=CanonicalEntityTaxonomyEntryResponse)
def dismiss_taxonomy_entity(
    entity_type: str,
    payload: DismissEntityRequest,
    db: Session = Depends(get_db),
) -> CanonicalEntityTaxonomyEntryResponse:
    try:
        row = upsert_entity(
            db,
            owner_id=settings.owner_id,
            entity_type=entity_type,
            display_name=payload.display_name,
            canonical_name=None,
            aliases=[],
            occurrence_count=0,
            status="dismissed",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Approved/dismissed entries are what load_role_taxonomy reads, so the cached
    # vocabulary must drop the moment the human changes it.
    clear_role_taxonomy_cache()
    return _serialize_canonical_entity(row)


def _classify_pending_for_scope(db: Session, scope: str) -> list[Recommendation]:
    if scope == SKILL_SCOPE:
        return classify_skills(_list_pending_unknown_skills(db))
    return classify_entities(list_pending_entities(db, owner_id=settings.owner_id, entity_type=scope))


def _serialize_bulk_review_recommendation(item: Recommendation) -> BulkReviewRecommendation:
    return BulkReviewRecommendation(
        key=item.key,
        display_name=item.display_name,
        occurrence_count=item.occurrence_count,
        candidate_ids=list(item.candidate_ids),
        bucket=item.bucket,
        reason=item.reason,
        source=item.source,
        locked=item.locked,
    )


@app.post("/settings/taxonomy/bulk-review/classify", response_model=BulkReviewClassifyResponse)
def classify_taxonomy_bulk_review(
    payload: BulkReviewClassifyRequest,
    db: Session = Depends(get_db),
) -> BulkReviewClassifyResponse:
    """Bucket every pending record for one scope. Writes nothing."""
    recommendations = _classify_pending_for_scope(db, payload.scope)
    model_used: str | None = None
    model_error: str | None = None
    if payload.use_model:
        if settings.deepseek_api_key:
            recommendations, model_error = refine_with_model(recommendations, scope=payload.scope)
            # Reported whenever the model ran, not only on a clean run. Batches fail
            # independently now, so a partial refinement still has model verdicts in
            # it and the card must not attribute those to the rules.
            model_used = settings.deepseek_model_fast
        else:
            model_error = "DeepSeek API key is missing; showing rules-only recommendations."
    return BulkReviewClassifyResponse(
        scope=payload.scope,
        total_pending=len(recommendations),
        counts=bucket_counts(recommendations),
        model_used=model_used,
        model_error=model_error,
        recommendations=[_serialize_bulk_review_recommendation(item) for item in recommendations],
    )


@app.post("/settings/taxonomy/bulk-review/apply", response_model=BulkReviewApplyResponse)
def apply_taxonomy_bulk_review(
    payload: BulkReviewApplyRequest,
    db: Session = Depends(get_db),
) -> BulkReviewApplyResponse:
    """Approve or dismiss exactly the confirmed keys, or write nothing at all."""
    if len(payload.keys) > MAX_APPLY_KEYS:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_APPLY_KEYS} records can be applied in one request",
        )
    if not runtime_state.taxonomy_bulk_review_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A bulk review apply is already running")
    try:
        # Re-derived, never trusted from the request body. The browser's copy can be
        # minutes old, and this is the only thing standing between a stale preview
        # and a write.
        recommendations = _classify_pending_for_scope(db, payload.scope)
        try:
            applicable, skipped = select_applicable(
                recommendations,
                keys=payload.keys,
                action=payload.action,
                expected_count=payload.expected_count,
            )
        except BulkReviewCountMismatch as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        status = "approved" if payload.action == "approve" else "dismissed"
        applied_names: list[str] = []
        for item in applicable:
            try:
                if payload.scope == SKILL_SCOPE:
                    _upsert_custom_skill_entry(
                        db,
                        skill_name=item.display_name,
                        canonical_name=item.display_name,
                        aliases=[],
                        category="custom",
                        cluster_hint=None,
                        occurrence_count=item.occurrence_count,
                        status=status,
                        auto_commit=False,
                    )
                else:
                    upsert_entity(
                        db,
                        owner_id=settings.owner_id,
                        entity_type=payload.scope,
                        display_name=item.display_name,
                        canonical_name=None,
                        aliases=[],
                        occurrence_count=item.occurrence_count,
                        status=status,
                        auto_commit=False,
                    )
            except (ValueError, HTTPException):
                # Both writers validate the name before touching the session, so a
                # rejected record leaves nothing half-written. One unusable name
                # must not cost the other 1,999 their write.
                skipped.append({"key": item.key, "reason": "invalid_name"})
                continue
            applied_names.append(item.display_name)

        if applied_names:
            db.commit()
            if payload.scope == SKILL_SCOPE:
                clear_skill_taxonomy_cache()
            else:
                clear_role_taxonomy_cache()
        return BulkReviewApplyResponse(
            scope=payload.scope,
            action=payload.action,
            applied_count=len(applied_names),
            applied_names=applied_names,
            skipped=skipped,
        )
    finally:
        runtime_state.taxonomy_bulk_review_lock.release()


@app.get("/settings/taxonomy/metrics", response_model=TaxonomyMetricsResponse)
def taxonomy_learning_metrics(db: Session = Depends(get_db)) -> TaxonomyMetricsResponse:
    rows = (
        db.query(RecruiterEmail.skills_json, RecruiterEmail.parser_details_json)
        .filter(RecruiterEmail.owner_id == settings.owner_id)
        .all()
    )
    unknown_emails = 0
    for skills_json, parser_details_json in rows:
        skills_payload = _json_object(skills_json) or {}
        parser_payload = _json_object(parser_details_json) or {}
        unknown = skills_payload.get("unknown") or parser_payload.get("unknown_skills")
        if isinstance(unknown, list) and unknown:
            unknown_emails += 1
    parsed_count = len(rows)
    return TaxonomyMetricsResponse(
        parsed_email_count=parsed_count,
        emails_with_unknown_skills=unknown_emails,
        unknown_skill_rate=round(unknown_emails / parsed_count, 4) if parsed_count else 0.0,
        pending_skill_count=len(_list_pending_unknown_skills(db)),
        pending_company_count=len(list_pending_entities(db, owner_id=settings.owner_id, entity_type="company")),
        pending_location_count=len(list_pending_entities(db, owner_id=settings.owner_id, entity_type="location")),
        alias_collision_count=len(load_skill_taxonomy().ambiguous_aliases),
    )


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


@app.post("/settings/job-intent-learning/{entry_id}/toggle-polarity", response_model=JobIntentTaxonomyEntryResponse)
def toggle_job_intent_polarity(entry_id: int, db: Session = Depends(get_db)) -> JobIntentTaxonomyEntryResponse:
    entry = (
        db.query(JobIntentTaxonomyEntry)
        .filter(JobIntentTaxonomyEntry.id == entry_id, JobIntentTaxonomyEntry.owner_id == settings.owner_id)
        .first()
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="job_intent_entry_not_found")
    entry.polarity = NEGATIVE_NEWSLETTER if entry.polarity == POSITIVE_RECRUITER_JD else POSITIVE_RECRUITER_JD
    db.commit()
    db.refresh(entry)
    clear_job_intent_signal_embedding_cache()
    return _serialize_job_intent_entry(entry)


@app.get("/settings/job-intent-learning/embedded", response_model=list[EmbeddedJobIntentSignalResponse])
def list_embedded_job_intent_signals(db: Session = Depends(get_db)) -> list[EmbeddedJobIntentSignalResponse]:
    approved = approved_learning_signals_for_owner(db, settings.owner_id)
    positive, negative = prioritized_learning_signals(approved)
    selected = [*positive, *negative]
    if not selected:
        return []
    _vectors, provider = generate_embeddings([signal.phrase for signal in selected])
    embedded = provider == "sbert"
    return [
        EmbeddedJobIntentSignalResponse(
            id=signal.id,
            phrase=signal.phrase,
            polarity=signal.polarity,
            confidence=signal.confidence,
            embedded=embedded,
        )
        for signal in selected
    ]


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
    user_settings = _get_settings(db) if hasattr(db, "query") else None
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
    groq_enabled_in_settings = bool(getattr(user_settings, "feature_groq_job_parser_enabled", False))
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
    elif settings.intent_gate_provider != "groq":
        groq_detail = (
            f"Groq is idle: the intent gate is running on {settings.intent_gate_provider}. "
            "Groq stays configured as the one-setting rollback."
        )
    elif groq_request_mode == "json_object":
        groq_detail = "Groq is configured in json_object compatibility mode for the current model."
    else:
        groq_detail = "Groq is configured in structured json_schema mode, but no Groq attempt has been recorded in this process yet."

    intent_gate_provider = settings.intent_gate_provider
    intent_gate_enabled_in_settings = groq_enabled_in_settings
    if intent_gate_provider == "groq":
        intent_gate_model = settings.groq_gate_model or "llama-3.1-8b-instant"
        intent_gate_configured = groq_configured
    elif intent_gate_provider == "deepseek":
        intent_gate_model = (
            settings.intent_gate_model or settings.deepseek_model_fast or "deepseek-v4-flash"
        )
        intent_gate_configured = bool(settings.deepseek_api_key) and bool(settings.deepseek_base_url)
    else:
        intent_gate_model = "rules_taxonomy"
        # The taxonomy needs no credentials, so "configured" is unconditionally true.
        intent_gate_configured = True

    intent_gate_runtime_healthy: bool | None
    if runtime_state.intent_gate_last_success_at and (
        runtime_state.intent_gate_last_attempted_at is None
        or runtime_state.intent_gate_last_success_at >= runtime_state.intent_gate_last_attempted_at
    ) and not runtime_state.intent_gate_last_error:
        intent_gate_runtime_healthy = True
    elif runtime_state.intent_gate_last_error:
        intent_gate_runtime_healthy = False
    else:
        intent_gate_runtime_healthy = None

    if not intent_gate_enabled_in_settings:
        intent_gate_detail = "The smart job-intent gate is turned off in settings; the rules taxonomy decides every email."
    elif intent_gate_provider == "taxonomy":
        intent_gate_detail = "The intent gate is pinned to the rules taxonomy; no model is called."
    elif not intent_gate_configured:
        intent_gate_detail = (
            f"The intent gate is set to {intent_gate_provider}, but that provider's API key or base URL is missing."
        )
    elif intent_gate_runtime_healthy is True:
        intent_gate_detail = f"Intent gate healthy on {intent_gate_provider} ({intent_gate_model})."
    elif intent_gate_runtime_healthy is False:
        intent_gate_detail = (
            f"Intent gate fell back to the rules taxonomy: {runtime_state.intent_gate_last_error}."
        )
    else:
        intent_gate_detail = (
            f"Intent gate is configured for {intent_gate_provider}, but no attempt has been recorded in this process yet."
        )

    return AIStatusResponse(
        configured=configured,
        connected=connected,
        running=ai_running,
        provider="deepseek",
        model=settings.deepseek_model_fast or "deepseek-v4-flash",
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
        intent_gate_provider=intent_gate_provider,
        intent_gate_model=intent_gate_model,
        intent_gate_configured=intent_gate_configured,
        intent_gate_enabled_in_settings=intent_gate_enabled_in_settings,
        intent_gate_runtime_healthy=intent_gate_runtime_healthy,
        intent_gate_last_error=runtime_state.intent_gate_last_error,
        intent_gate_detail=intent_gate_detail,
        intent_gate_effort_ladder=settings.intent_gate_effort_ladder,
        intent_gate_last_rung=runtime_state.intent_gate_last_rung,
        intent_gate_min_taxonomy_confidence=settings.intent_gate_min_taxonomy_confidence,
        intent_gate_last_attempted_at=runtime_state.intent_gate_last_attempted_at,
        intent_gate_last_success_at=runtime_state.intent_gate_last_success_at,
        intent_gate_last_duration_ms=runtime_state.intent_gate_last_duration_ms,
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
            entity_type=payload.entity_type,
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
            entity_type=payload.entity_type,
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
    # Annotated rather than `= Query(None)`: the plain default means calling
    # this function directly in Python (as the timezone tests do) receives
    # None and resolves the default bucket, instead of receiving a Query
    # object that then fails validation as an unknown bucket.
    bucket: Annotated[str | None, Query()] = None,
    db: Session = Depends(get_db),
) -> ProductivityTrendResponse:
    if range not in RANGE_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid range")
    resolved_bucket = bucket or analytics_service.default_bucket_for_range(range)
    if resolved_bucket not in BUCKET_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid bucket")

    start, end = _range_bounds(range)
    start = _ensure_utc(start)
    end = _ensure_utc(end)

    # The bucketing loop lives in analytics_service so this route and the
    # chat's get_chart tool cannot drift apart.
    bars = [
        ProductivityBarPoint(
            ts=row["ts"],
            sent_count=row["sent_count"],
            failed_count=row["failed_count"],
            needs_review_count=row["needs_review_count"],
            recent_run_count=row["recent_run_count"],
        )
        for row in analytics_service.productivity_trend_bars(
            db, owner_id=settings.owner_id, start=start, end=end, bucket=resolved_bucket
        )
    ]

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

    previous_total_sent = analytics_service.previous_period_sent_count(
        db, owner_id=settings.owner_id, start=start, end=end
    )
    delta = current_total_sent - previous_total_sent
    direction = "flat"
    if delta > 0:
        direction = "up"
    elif delta < 0:
        direction = "down"
    delta_pct = round((delta / float(max(previous_total_sent, 1))) * 100, 2)

    return ProductivityTrendResponse(
        range=range,
        bucket=resolved_bucket,
        trend_direction=direction,
        trend_delta_pct=delta_pct,
        kpi_total_sent=current_total_sent,
        previous_period_total_sent=previous_total_sent,
        bars=bars,
    )


def _update_manifest_run_counts(db: Session, run_key: str | None, source_rows: list[RecruiterEmail]) -> None:
    if not run_key:
        return
    recent_run = db.query(RecentRun).filter(RecentRun.owner_id == settings.owner_id, RecentRun.run_key == run_key).first()
    if recent_run is None:
        return
    recent_run.source_count = len(source_rows)
    recent_run.requirement_count = sum(
        max(1, int(row.requirement_count or 1)) for row in source_rows
    )
    recent_run.multi_role_source_count = sum(1 for row in source_rows if row.role_manifest_status == "multiple")
    recent_run.manifest_review_count = sum(
        1 for row in source_rows if row.role_manifest_status in {"invalid", "uncertain"}
    )
    db.commit()


def _run_gmail_sync(
    db: Session,
    *,
    sync_batch_id: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> GmailSyncResponse:
    response = _get_orchestration_service().sync_gmail(
        db,
        sync_batch_id=sync_batch_id,
        progress_callback=progress_callback,
    )
    user_settings = _get_settings(db)
    if user_settings.feature_role_manifest_enabled:
        rows = db.query(RecruiterEmail).filter(RecruiterEmail.sync_batch_id == response.sync_batch_id).all()
        for row in rows:
            # role_manifest_status is already stamped inline during sync_gmail for rows the
            # new detect-before-parse ordering handled; this loop is now only a safety net.
            if not row.is_multi_role_child and row.role_manifest_status is None:
                try:
                    _retry_role_detection(row.id, db, max_rung=2)
                except Exception:
                    db.rollback()
                    logger.exception(
                        "role_manifest_retry_failed source=gmail email_id=%s sync_batch_id=%r",
                        row.id,
                        response.sync_batch_id,
                    )
        _update_manifest_run_counts(db, response.run_key, rows)
    return response


def _run_automation(
    payload: AutomationRunRequest | None,
    db: Session,
    *,
    run_key_override: str | None = None,
    items_override: list[GmailMessageCandidate] | None = None,
) -> AutomationRunResponse:
    response = _get_orchestration_service().run_once(
        payload, db, run_key_override=run_key_override, items_override=items_override
    )
    user_settings = _get_settings(db)
    if user_settings.feature_role_manifest_enabled and response.queued_email_ids:
        source_rows = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.id.in_(response.queued_email_ids))
            .all()
        )
        auto_sent_count = response.auto_sent_count or 0
        auto_send_failed_count = response.auto_send_failed_count or 0
        dry_run = policy_service.policy_dry_run(
            policy_service.read_policy_from_settings(user_settings.policy_json)
        )
        for source_row in source_rows:
            if source_row.is_multi_role_child:
                continue
            # role_manifest_status is already stamped inline during run_once for rows the
            # new detect-before-parse ordering handled; only call retry as a safety net.
            if source_row.role_manifest_status is None:
                try:
                    detection = _retry_role_detection(source_row.id, db, max_rung=2)
                except Exception:
                    db.rollback()
                    logger.exception(
                        "role_manifest_retry_failed source=automation email_id=%s run_key=%r",
                        source_row.id,
                        response.run_key,
                    )
                    continue
                manifest_status = detection.manifest_status
            else:
                manifest_status = source_row.role_manifest_status
            if user_settings.feature_auto_send and not dry_run and manifest_status in {"single", "single_fallback"}:
                try:
                    _get_orchestration_service().approve_send(
                        source_row.id,
                        ApproveSendRequest(),
                        db,
                    )
                    auto_sent_count += 1
                except HTTPException:
                    auto_send_failed_count += 1
        if user_settings.feature_auto_send:
            response.auto_sent_count = auto_sent_count
            response.auto_send_failed_count = auto_send_failed_count
        _update_manifest_run_counts(db, response.run_key, source_rows)
    return response


def _run_nvoids_sync(
    db: Session,
    *,
    max_items: int,
    run_key_override: str | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    role_manifest_enabled: bool | None = None,
    criteria_override=None,
):
    if role_manifest_enabled is None:
        role_manifest_enabled = bool(_get_settings(db).feature_role_manifest_enabled)
    existing_source_ids = {
        row_id
        for (row_id,) in (
            db.query(RecruiterEmail.id)
            .filter(RecruiterEmail.source == "nvoids")
            .all()
        )
    }
    result = external_feed_service.sync_nvoids(
        db,
        owner_id=settings.owner_id,
        max_items=max_items,
        run_key_override=run_key_override,
        progress_callback=progress_callback,
        criteria_override=criteria_override,
    )
    if role_manifest_enabled:
        rows = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.source == "nvoids")
            .all()
        )
        rows = [row for row in rows if row.id not in existing_source_ids and not row.is_multi_role_child]
        for row in rows:
            try:
                _retry_role_detection(row.id, db, max_rung=2)
            except Exception:
                db.rollback()
                logger.exception(
                    "role_manifest_retry_failed source=nvoids email_id=%s external_message_id=%r run_key=%r",
                    row.id,
                    row.external_message_id,
                    result.run_key,
                )
        _update_manifest_run_counts(db, result.run_key, rows)
    return result


@app.get("/jobs/health")
def jobs_health() -> dict[str, object]:
    try:
        ready = redis_is_ready()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"job_queue_unavailable: {exc}") from exc
    return {"status": "ok" if ready else "unavailable", "redis_ready": ready}


@app.get("/jobs/summary", response_model=JobQueueSummaryResponse)
def jobs_summary(db: Session = Depends(get_db)) -> JobQueueSummaryResponse:
    queued = 0
    processing = 0
    try:
        connection = get_redis_connection()
        for name in (GMAIL_SYNC_QUEUE, NVOIDS_SYNC_QUEUE, AUTOMATION_RUN_QUEUE):
            queue = get_queue(name, connection=connection)
            queued += queue.count
            processing += StartedJobRegistry(name=name, connection=connection).count
    except Exception:
        logger.exception("jobs_summary_redis_unavailable")
    succeeded = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == settings.owner_id, RecentRun.status == "ok")
        .count()
    )
    failed = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == settings.owner_id, RecentRun.status == "failed")
        .count()
    )
    return JobQueueSummaryResponse(queued=queued, processing=processing, succeeded=succeeded, failed=failed)


@app.get("/gmail/live-replies", response_model=LiveReplyStatusResponse)
def live_replies() -> LiveReplyStatusResponse:
    return LiveReplyStatusResponse(
        count=runtime_state.live_reply_count,
        checked_at=runtime_state.live_reply_checked_at,
    )


@app.post("/jobs/gmail-sync", response_model=JobEnqueueResponse, status_code=202)
def enqueue_gmail_sync(db: Session = Depends(get_db)) -> JobEnqueueResponse:
    return _enqueue_gmail_sync(db)


@app.post("/jobs/nvoids-sync", response_model=JobEnqueueResponse, status_code=202)
def enqueue_nvoids_sync(
    batch_limit: int | None = Query(default=None, ge=1, le=50),
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    user_settings = _get_settings(db)
    if not user_settings.feature_nvoids_enabled:
        raise HTTPException(status_code=400, detail="Nvoids sync is disabled in settings")
    resolved_batch_limit = batch_limit if batch_limit is not None else _nvoids_batch_limit(user_settings)
    return _enqueue_nvoids_sync(db, max_items=resolved_batch_limit)


@app.post("/jobs/nvoids-client-search", response_model=JobEnqueueResponse, status_code=202)
def enqueue_nvoids_client_search(
    payload: NvoidsClientSearchRequest,
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    """Start the one search a confirmed `propose_nvoids_search` card describes.

    The other half of that tool, which has been proposing searches with nothing
    to confirm to: `_enqueue_nvoids_client_search` was written and never routed,
    so the card had no endpoint and the dashboard had no handler. A proposal the
    user cannot accept is worse than no proposal - the model announces a search
    is ready, the click does nothing, and the assistant's prose is the only
    account of the turn.

    The user's click is what starts it. The tool cannot: it returns
    `started: false` and this is the only caller of the enqueue.
    """
    user_settings = _get_settings(db)
    if not user_settings.feature_nvoids_enabled:
        raise HTTPException(status_code=400, detail="Nvoids sync is disabled in settings")
    # Rebuilt from the criteria rather than trusted from the request. The card
    # showed `generated_query`; what actually runs is composed here.
    criteria = nvoids_search_job.SearchCriteria(
        end_client=payload.end_client.strip(),
        job_role=payload.job_role.strip(),
        search_location=payload.search_location.strip(),
        query_mode=payload.query_mode,
        batch_limit=payload.batch_limit,
    )
    return _enqueue_nvoids_client_search(db, criteria=criteria.as_dict())


@app.post("/jobs/automation-run", response_model=JobEnqueueResponse, status_code=202)
def enqueue_automation_run(
    payload: AutomationRunRequest | None = None,
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    return _enqueue_automation(payload, db)


@app.get("/jobs/{run_key}", response_model=JobStatusResponse)
def job_status(run_key: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    row = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == settings.owner_id, RecentRun.run_key == run_key)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    payload = row_to_recent_run_dict(row)
    if row.job_backend_id:
        try:
            backend_status = Job.fetch(
                row.job_backend_id,
                connection=get_redis_connection(),
            ).get_status(refresh=True)
            if backend_status == JobStatus.STARTED:
                payload["status"] = "running"
            elif backend_status in {
                JobStatus.QUEUED,
                JobStatus.DEFERRED,
                JobStatus.SCHEDULED,
            }:
                payload["status"] = "queued"
            elif backend_status in {JobStatus.CANCELED, JobStatus.STOPPED}:
                payload["status"] = "canceled"
            elif backend_status == JobStatus.FAILED:
                payload["status"] = "failed"
        except NoSuchJobError:
            logger.warning("job_status_backend_record_missing run_key=%r job_id=%r", run_key, row.job_backend_id)
        except Exception:
            logger.exception("job_status_backend_lookup_failed run_key=%r job_id=%r", run_key, row.job_backend_id)
    return JobStatusResponse(**payload, job_id=row.job_backend_id)


@app.post("/jobs/{run_key}/cancel", response_model=JobStatusResponse)
def cancel_job(run_key: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    row = (
        db.query(RecentRun)
        .filter(RecentRun.owner_id == settings.owner_id, RecentRun.run_key == run_key)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    job: Job | None = None
    backend_status: JobStatus | None = None
    if row.job_backend_id:
        try:
            job = Job.fetch(row.job_backend_id, connection=get_redis_connection())
            backend_status = job.get_status(refresh=True)
        except NoSuchJobError:
            logger.warning("job_cancel_backend_record_missing run_key=%r job_id=%r", run_key, row.job_backend_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"job_cancel_failed: {exc}") from exc
    backend_cancelable = backend_status in {
        JobStatus.QUEUED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
        JobStatus.STARTED,
    }
    if row.status not in {"queued", "running"} and not backend_cancelable:
        raise HTTPException(status_code=409, detail=f"job_not_cancelable:{row.status}")
    if job is not None:
        try:
            if backend_status == JobStatus.STARTED:
                send_stop_job_command(job.connection, job.id)
            else:
                job.cancel()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"job_cancel_failed: {exc}") from exc
    row.status = "canceled"
    row.detail = "Background job canceled."
    db.commit()
    db.refresh(row)
    return JobStatusResponse(**row_to_recent_run_dict(row), job_id=row.job_backend_id)


@app.post("/gmail/sync", response_model=GmailSyncResponse)
def gmail_sync(db: Session = Depends(get_db)) -> GmailSyncResponse:
    return _run_gmail_sync(db)


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


def _recruiter_opportunity_response(
    row: RecruiterOpportunity,
    recruiter: PremiumNumberContact | None,
    record_id: str | None = None,
) -> RecruiterOpportunityResponse:
    return RecruiterOpportunityResponse(
        id=row.id,
        recruiter_number_id=row.recruiter_number_id,
        source_email_id=row.source_email_id,
        gmail_message_id=row.gmail_message_id,
        source_type=row.source_type or "gmail",
        source_url=row.source_url,
        external_opportunity_id=row.external_opportunity_id,
        email_id=row.source_email_id or row.external_opportunity_id,
        record_id=record_id,
        email_subject=row.email_subject,
        email_sender=row.email_sender,
        gmail_open_url=row.gmail_open_url,
        received_at=row.received_at,
        job_title=row.job_title,
        end_client=row.end_client,
        location=row.location,
        work_mode=row.work_mode,
        visa_restrictions=row.visa_restrictions,
        resume_file_name=row.resume_file_name,
        implementation_partner=row.implementation_partner,
        prime_vendor=row.prime_vendor,
        domain=row.domain,
        extracted_skills=row.extracted_skills,
        evidence=row.evidence,
        recruiter_name=(recruiter.recruiter_name if recruiter else ""),
        recruiter_email=(recruiter.recruiter_email if recruiter else ""),
        recruiter_phone_display=(recruiter.display_phone_number if recruiter else ""),
        recruiter_phone_normalized=(recruiter.normalized_phone_number or "" if recruiter else ""),
        recruiter_company=(recruiter.company if recruiter else ""),
        linkedin_url=(recruiter.linkedin_url if recruiter else ""),
        status=row.status,
        notes=row.notes,
        employment_type=row.employment_type,
        rate_amount=row.rate_amount,
        rate_currency=row.rate_currency,
        rate_unit=row.rate_unit,
        contract_duration=row.contract_duration,
        relocation_required=row.relocation_required,
        extension_likely=row.extension_likely,
        end_client_confirmed=row.end_client_confirmed,
        job_confidence=row.job_confidence,
        cold_call_script=row.cold_call_script,
        cold_call_script_updated_at=row.cold_call_script_updated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _get_application(db: Session, application_id: int) -> Application:
    row = (
        db.query(Application)
        .filter(
            Application.owner_id == settings.owner_id,
            Application.id == application_id,
            Application.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Application not found")
    return row


def _get_appts_application(db: Session, application_id: int) -> AppTSApplication:
    row = db.query(AppTSApplication).filter(AppTSApplication.owner_id == settings.owner_id, AppTSApplication.id == application_id, AppTSApplication.deleted_at.is_(None)).first()
    if not row:
        raise HTTPException(status_code=404, detail="AppTS application not found")
    return row


def _get_application_rtr(db: Session, application: Application, rtr_id: int) -> ApplicationRTR:
    row = (
        db.query(ApplicationRTR)
        .filter(
            ApplicationRTR.owner_id == settings.owner_id,
            ApplicationRTR.application_id == application.id,
            ApplicationRTR.id == rtr_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="RTR not found")
    return row


def _get_application_interview(
    db: Session,
    application: Application,
    interview_id: int,
) -> ApplicationInterview:
    row = (
        db.query(ApplicationInterview)
        .filter(
            ApplicationInterview.owner_id == settings.owner_id,
            ApplicationInterview.application_id == application.id,
            ApplicationInterview.id == interview_id,
            ApplicationInterview.deleted_at.is_(None),
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Interview not found")
    return row


def _get_application_suggestion(db: Session, suggestion_id: int) -> ApplicationSuggestion:
    row = (
        db.query(ApplicationSuggestion)
        .filter(
            ApplicationSuggestion.owner_id == settings.owner_id,
            ApplicationSuggestion.id == suggestion_id,
            ApplicationSuggestion.status == "pending",
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Suggestion not found or already resolved")
    return row


def _require_resume_tracking_enabled(db: Session) -> UserSettings:
    user_settings = _get_settings(db)
    if not user_settings.feature_resume_tracking_enabled:
        raise HTTPException(status_code=403, detail="Resume tracking is disabled")
    return user_settings


def _require_applications_enabled(db: Session) -> UserSettings:
    user_settings = _get_settings(db)
    if not user_settings.feature_applications_enabled:
        raise HTTPException(status_code=403, detail="Application tracking is disabled")
    return user_settings


def _skill_gap_response(row: ApplicationSkillGapSnapshot) -> ApplicationSkillGapResponse:
    return ApplicationSkillGapResponse(
        source=row.source,
        matched_required=_json_string_list(row.matched_required_json),
        missing_required=_json_string_list(row.missing_required_json),
        matched_preferred=_json_string_list(row.matched_preferred_json),
        missing_preferred=_json_string_list(row.missing_preferred_json),
        computed_at=row.computed_at,
    )


def _application_suggestion_response(row: ApplicationSuggestion) -> ApplicationSuggestionResponse:
    return ApplicationSuggestionResponse.model_validate(row).model_copy(
        update={"payload": _json_object(row.payload_json) or {}}
    )


def _milestones_response(raw: str | None) -> dict[str, datetime]:
    parsed: dict[str, datetime] = {}
    for name, value in (_json_object(raw) or {}).items():
        try:
            timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        parsed[name] = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    return parsed


def _application_response(
    db: Session,
    row: Application,
    *,
    include_events: bool = False,
    models: application_service.ApplicationModels = application_service.LEGACY_MODELS,
) -> ApplicationResponse:
    opportunity = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.id == row.recruiter_opportunity_id,
        )
        .first()
    )
    source_email_id = getattr(row, "source_recruiter_email_id", None)
    source_email = (
        db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == source_email_id).first()
        if source_email_id else None
    )
    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == row.recruiter_contact_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    events = (
        db.query(models.event_cls)
        .filter(
            models.event_cls.owner_id == settings.owner_id,
            models.event_cls.application_id == row.id,
        )
        .order_by(models.event_cls.occurred_at.asc(), models.event_cls.id.asc())
        .all()
        if include_events
        else []
    )
    rtr_history = (
        db.query(models.rtr_cls)
        .filter(
            models.rtr_cls.owner_id == settings.owner_id,
            models.rtr_cls.application_id == row.id,
        )
        .order_by(models.rtr_cls.requested_at.desc(), models.rtr_cls.id.desc())
        .all()
        if include_events
        else []
    )
    interviews = (
        db.query(models.interview_cls)
        .filter(
            models.interview_cls.owner_id == settings.owner_id,
            models.interview_cls.application_id == row.id,
            models.interview_cls.deleted_at.is_(None),
        )
        .order_by(
            models.interview_cls.scheduled_at.is_(None),
            models.interview_cls.scheduled_at.asc(),
            models.interview_cls.id.asc(),
        )
        .all()
        if include_events
        else []
    )
    skill_gap = (
        db.query(models.skill_gap_snapshot_cls)
        .filter(
            models.skill_gap_snapshot_cls.owner_id == settings.owner_id,
            models.skill_gap_snapshot_cls.application_id == row.id,
        )
        .first()
    )
    try:
        rejection_detail_tags = json.loads(row.rejection_detail_tags_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        rejection_detail_tags = []
    try:
        resume_skills_snapshot = json.loads(row.resume_skills_snapshot_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        resume_skills_snapshot = []
    return ApplicationResponse.model_validate(row).model_copy(
        update={
            "resume_skills_snapshot": resume_skills_snapshot if isinstance(resume_skills_snapshot, list) else [],
            "record_id": opportunity_lineage_service.record_id_for_application(
                db,
                owner_id=settings.owner_id,
                application=row,
            ),
            "current_recruiter_name": recruiter.recruiter_name if recruiter else "",
            "current_recruiter_company": recruiter.company if recruiter else "",
            "current_recruiter_phone_display": recruiter.display_phone_number if recruiter else "",
            "current_recruiter_email": recruiter.recruiter_email if recruiter else "",
            "current_recruiter_linkedin_url": recruiter.linkedin_url if recruiter else "",
            "current_job_title": opportunity.job_title if opportunity else "",
            "current_end_client": opportunity.end_client if opportunity else "",
            "current_recruiter_categories": _contact_categories(recruiter) if recruiter else [],
            "current_recruiter_status": _contact_status(recruiter) if recruiter else "",
            "current_recruiter_verification_level": recruiter.recruiter_verification_level if recruiter else "",
            "current_source_url": (opportunity.source_url or opportunity.gmail_open_url or None) if opportunity else None,
            "source_recruiter_email_id": source_email_id,
            "ats_score": source_email.ats_score if source_email else None,
            "ats_summary": source_email.ats_summary if source_email else None,
            "sent_gmail_message_link": source_email.gmail_sent_message_url if source_email else None,
            "events": [ApplicationEventResponse.model_validate(event) for event in events],
            "rtr_history": [ApplicationRTRResponse.model_validate(rtr) for rtr in rtr_history],
            "interviews": [ApplicationInterviewResponse.model_validate(interview) for interview in interviews],
            "is_manual_entry": row.recruiter_opportunity_id is None,
            "rejection_detail_tags": rejection_detail_tags if isinstance(rejection_detail_tags, list) else [],
            "milestones_reached": _milestones_response(row.milestones_reached_json),
            "skill_gap": _skill_gap_response(skill_gap) if skill_gap else None,
        }
    )


@app.post("/automation/run-once", response_model=AutomationRunResponse)
def automation_run_once(payload: AutomationRunRequest | None = None, db: Session = Depends(get_db)) -> AutomationRunResponse:
    return _run_automation(payload, db)


@app.post("/manual-requirements/preview", response_model=ManualRequirementPreviewResponse)
def preview_manual_requirement(
    payload: ManualRequirementPreviewRequest,
    db: Session = Depends(get_db),
) -> ManualRequirementPreviewResponse:
    """Has this exact requirement already been pasted? No model calls.

    Runs on blur while the user is still typing, so it stays cheap: contact
    extraction, a rules parse and one indexed lookup.
    """
    duplicate = _get_manual_intake_service().preview(db, text=_manual_intake_text(payload.text))
    if duplicate is None:
        return ManualRequirementPreviewResponse()
    return ManualRequirementPreviewResponse(
        duplicate_of=ManualDuplicateSummary(
            id=duplicate.id,
            role=duplicate.role,
            client=duplicate.client,
            created_at=duplicate.created_at,
        )
    )


@app.post("/manual-requirements", response_model=JobEnqueueResponse)
def create_manual_requirement(
    payload: ManualRequirementCreateRequest,
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    """Queue a pasted requirement for ingestion.

    Returns a run key, not a card: extraction, scoring and drafting are several
    model calls. The client polls GET /jobs/{run_key}, the same endpoint the
    Gmail and Nvoids syncs already report through.

    `acknowledged_duplicate_of` is recorded and never enforced - a recruiter
    re-sending an updated requirement is normal, and the warning exists to be
    seen, not to block.
    """
    text = _manual_intake_text(payload.text)
    if payload.acknowledged_duplicate_of:
        logger.info(
            "manual_intake_duplicate_acknowledged existing_email_id=%s",
            payload.acknowledged_duplicate_of,
        )
    return _enqueue_manual_intake(db, text=text)


@app.post("/manual-requirements/from-chat", response_model=JobEnqueueResponse)
def create_manual_requirement_from_chat(
    payload: ManualRequirementFromChatRequest,
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    """Queue a requirement the user pasted or attached in chat.

    The sibling route takes text. This one takes an id and reads the text back
    from storage, so what gets ingested is the row the card was built from.

    Exactly one id is guaranteed by the request model. This route must never
    fall back to the newest message because the card must bind to the row it
    showed.
    """
    document = user_supplied_document(
        db,
        settings.owner_id,
        attachment_id=payload.attachment_id,
        message_id=payload.message_id,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="That message or file is no longer readable.")
    text = _manual_intake_text(document.text)
    if payload.acknowledged_duplicate_of:
        logger.info(
            "manual_intake_duplicate_acknowledged existing_email_id=%s source=chat",
            payload.acknowledged_duplicate_of,
        )
    return _enqueue_manual_intake(db, text=text)


@app.get("/filter-options", response_model=FilterOptionsResponse)
def list_filter_options(
    bucket: str = Query(..., max_length=60),
    field: str = Query(..., max_length=60),
    q: str | None = Query(default=None, max_length=filter_options_service.MAX_QUERY_CHARS),
    limit: int = Query(filter_options_service.DEFAULT_LIMIT, ge=1, le=filter_options_service.MAX_LIMIT),
    db: Session = Depends(get_db),
) -> FilterOptionsResponse:
    if field not in filter_options_service.FILTER_OPTION_COLUMNS.get(bucket, {}):
        raise HTTPException(status_code=404, detail="Unknown filter option field")
    return FilterOptionsResponse(
        bucket=bucket,
        field=field,
        values=filter_options_service.distinct_values(
            db,
            settings.owner_id,
            bucket,
            field,
            q=q,
            limit=limit,
        ),
    )


def _text_search_active(*values: str | None) -> bool:
    """True when the caller typed a free-text/picker filter.

    Only text filters widen the date scope. Structural toggles (source,
    sendability, has_resume, score ranges) refine whatever is on screen and
    leave the day's scope alone; a text search is a request to find something
    wherever it lives. Keep this list in step with the `text`/`combobox` fields
    in dashboard/src/filterSortRegistry.ts - the frontend decides whether to
    show "searching all dates" from the same set.
    """
    return any(value and value.strip() for value in values)


# Score bounds behind the ATS strength badge on the cards. Mirrors getAtsStrengthLabel
# in dashboard/src/App.tsx - keep the two in step, the filter promises the badge.
ATS_STRENGTH_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "strong": (80.0, None),
    "moderate": (60.0, 80.0),
    "weak": (None, 60.0),
}

# Filter param -> the EmailResponse field carrying that badge. These three are derived
# after the query in _populate_badge_fields (premium-contact resolution plus the
# cross-referenced following state), so they are filtered in Python, not in SQL.
BADGE_FILTER_FIELDS = {
    "contact_status": "premium_status",
    "verification": "premium_verification_level",
    "following": "following_badge",
}


def _csv_values(raw: str | None) -> list[str]:
    return [value.strip().lower() for value in (raw or "").split(",") if value.strip()]


def _ats_strength_clause(raw: str | None):
    """OR of the score ranges for the requested tiers, or None when none was asked for.

    `unknown` is the unscored row - the case the badge itself labels "Unknown".
    """
    clauses = []
    for tier in _csv_values(raw):
        if tier == "unknown":
            clauses.append(RecruiterEmail.ats_score.is_(None))
            continue
        bounds = ATS_STRENGTH_BOUNDS.get(tier)
        if bounds is None:
            continue
        low, high = bounds
        clause = RecruiterEmail.ats_score.is_not(None)
        if low is not None:
            clause = clause & (RecruiterEmail.ats_score >= low)
        if high is not None:
            clause = clause & (RecruiterEmail.ats_score < high)
        clauses.append(clause)
    return or_(*clauses) if clauses else None


def _badge_filter_selection(**raw_values: str | None) -> dict[str, set[str]]:
    selection = {BADGE_FILTER_FIELDS[key]: set(_csv_values(value)) for key, value in raw_values.items()}
    return {field: values for field, values in selection.items() if values}


def _matches_badge_filters(values: Mapping[str, object], selection: dict[str, set[str]]) -> bool:
    # A card carrying no badge in a filtered dimension is not a match: asking for
    # "Active" asks for the cards showing that badge, not for every other card too.
    return all(str(values.get(field) or "").lower() in wanted for field, wanted in selection.items())


def _filter_rows_by_badges(
    db: Session,
    owner_id: str,
    rows: list[RecruiterEmail],
    selection: dict[str, set[str]],
) -> list[RecruiterEmail]:
    """Score already-ordered rows through the same badge pass the cards render from.

    Costs a full read of the filtered bucket, so it only runs when a badge filter is set.
    """
    badge_fields = _populate_badge_fields(db, owner_id, rows)
    return [row for row in rows if _matches_badge_filters(badge_fields.get(row.id, {}), selection)]


@app.get("/candidates", response_model=CandidateListResponse)
def list_candidates(
    state: str = Query("needs_review"),
    cursor: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("newest"),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    source: str | None = Query(default=None),
    sendability: str | None = Query(default=None),
    has_resume: bool | None = Query(default=None),
    min_ats_score: float | None = Query(default=None, ge=0, le=100),
    max_ats_score: float | None = Query(default=None, ge=0, le=100),
    role: str | None = Query(default=None, max_length=200),
    interview_type: str | None = Query(default=None, max_length=255),
    subject: str | None = Query(default=None, max_length=500),
    location: str | None = Query(default=None, max_length=200),
    sender: str | None = Query(default=None, max_length=255),
    recipient: str | None = Query(default=None, max_length=255),
    ats_strength: str | None = Query(default=None),
    contact_status: str | None = Query(default=None),
    verification: str | None = Query(default=None),
    following: str | None = Query(default=None),
    routing_status: str | None = Query(default=None),
    reason: str | None = Query(default=None, max_length=500),
    opened: bool | None = Query(default=None),
    company: str | None = Query(default=None, max_length=255),
    db: Session = Depends(get_db),
) -> CandidateListResponse:
    valid_sorts = {"newest", "oldest", "highest_score", "lowest_score"}
    if sort not in valid_sorts:
        raise HTTPException(status_code=422, detail=f"Invalid sort. Must be one of: {', '.join(sorted(valid_sorts))}")
    if min_ats_score is not None and max_ats_score is not None and min_ats_score > max_ats_score:
        raise HTTPException(status_code=422, detail="min_ats_score must be <= max_ats_score")
    states = [s.strip() for s in state.split(",") if s.strip()]
    query = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id)
    if states:
        query = query.filter(or_(*[RecruiterEmail.state == s for s in states]))

    # A text search is "find this wherever it is", so it overrides the implicit
    # one-day `mail_date` scope: typing java must reach every matching role, not
    # only today's. An explicit `date_filter` from the filter bar is a deliberate
    # choice and still wins. This also keeps /filter-options honest - the picker
    # offers values from the whole bucket, so the list has to search the whole
    # bucket or a suggestion can come back with zero rows.
    if _text_search_active(role, subject, location, sender, recipient, company, reason, interview_type):
        mail_date = None

    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        field = RecruiterEmail.sent_at if _is_approved_sent_only(states) else RecruiterEmail.gmail_received_at
        query = query.filter(field.is_not(None), field >= start, field < end)
    elif mail_date:
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

    if source:
        values = [value.strip() for value in source.split(",") if value.strip()]
        if values:
            query = query.filter(RecruiterEmail.source.in_(values))
    if sendability:
        statuses = set().union(*(SENDABILITY_BUCKETS.get(value.strip(), frozenset()) for value in sendability.split(",")))
        if statuses:
            query = query.filter(RecruiterEmail.sendability_status.in_(statuses))
    if has_resume is not None:
        clause = RecruiterEmail.resume_file_name.is_not(None) & (RecruiterEmail.resume_file_name != "")
        query = query.filter(clause if has_resume else ~clause)
    if min_ats_score is not None:
        query = query.filter(RecruiterEmail.ats_score >= min_ats_score)
    if max_ats_score is not None:
        query = query.filter(RecruiterEmail.ats_score <= max_ats_score)
    strength_clause = _ats_strength_clause(ats_strength)
    if strength_clause is not None:
        query = query.filter(strength_clause)
    for value, column in ((role, RecruiterEmail.role), (interview_type, RecruiterEmail.interview_type), (subject, RecruiterEmail.subject), (location, RecruiterEmail.location), (sender, RecruiterEmail.sender), (recipient, RecruiterEmail.recipient_email)):
        if value and value.strip():
            query = query.filter(column.ilike(f"%{value.strip()}%"))
    if routing_status:
        values = [value.strip() for value in routing_status.split(",") if value.strip()]
        if values:
            query = query.filter(RecruiterEmail.routing_status.in_(values))
    if reason and reason.strip():
        query = query.filter(RecruiterEmail.last_error.ilike(f"%{reason.strip()}%"))
    if opened is not None:
        query = query.filter(RecruiterEmail.open_count > 0 if opened else RecruiterEmail.open_count == 0)
    if company and company.strip():
        query = query.join(RecruiterOpportunity, RecruiterOpportunity.source_email_id == RecruiterEmail.id, isouter=True)
        query = query.filter(RecruiterOpportunity.end_client.ilike(f"%{company.strip()}%"))

    total = query.count()

    if sort == "highest_score":
        query = query.order_by(RecruiterEmail.ats_score.desc(), RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())
    elif sort == "lowest_score":
        query = query.order_by(RecruiterEmail.ats_score.asc(), RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())
    elif sort == "oldest" and _is_approved_sent_only(states):
        query = query.order_by(RecruiterEmail.sent_at.is_(None), RecruiterEmail.sent_at.asc(), RecruiterEmail.created_at.asc(), RecruiterEmail.id.asc())
    elif sort == "oldest":
        query = query.order_by(RecruiterEmail.created_at.asc(), RecruiterEmail.id.asc())
    elif _is_approved_sent_only(states):
        query = query.order_by(
            RecruiterEmail.sent_at.is_(None),
            RecruiterEmail.sent_at.desc(),
            RecruiterEmail.created_at.desc(),
            RecruiterEmail.id.desc(),
        )
    else:
        query = query.order_by(RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())

    badge_selection = _badge_filter_selection(contact_status=contact_status, verification=verification, following=following)
    if badge_selection:
        matching = _filter_rows_by_badges(db, settings.owner_id, query.all(), badge_selection)
        total = len(matching)
        visible, next_cursor, has_next = _paginate_items(matching, cursor=cursor, limit=limit)
    else:
        items = query.offset(cursor).limit(limit + 1).all()
        has_next = len(items) > limit
        visible = items[:limit]
        next_cursor = cursor + limit if has_next else None
    _hydrate_candidates_for_review(db, visible)
    badge_fields = _populate_badge_fields(db, settings.owner_id, visible)
    attachment_file_names = _enabled_attachment_file_names(db)
    serialized_items: list[EmailResponse] = []
    for item in visible:
        payload = EmailResponse.model_validate(item).model_dump()
        payload["attachment_file_names"] = attachment_file_names
        payload["parser_details"] = item.parser_details_json
        payload["resume_picker_candidates"] = _compact_resume_picker_candidates(payload["resume_picker_candidates"])
        payload["sendability_status"] = resolve_sendability_status(item)
        payload.update(badge_fields.get(item.id, {}))
        serialized_items.append(EmailResponse.model_validate(payload))
    return CandidateListResponse(
        items=serialized_items,
        next_cursor=next_cursor,
        has_next=has_next,
        total=total,
    )


def _ensure_gmail_labeling_service() -> GmailLabelingService:
    return gmail_labeling_runtime_service.ensure_service()


def _apply_gmail_label_for_email(
    *,
    email: RecruiterEmail,
    candidate_item: GmailMessageCandidate | dict[str, object],
) -> None:
    gmail_labeling_runtime_service.apply_for_email(email=email, candidate_item=candidate_item)


def _log_gmail_labeling_stats() -> None:
    gmail_labeling_runtime_service.log_stats()


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
        digits_only = re.sub(r"\D", "", q)
        phone_filters = [PremiumNumberLead.phone_number_display.ilike(like)]
        if digits_only:
            phone_filters.append(PremiumNumberLead.phone_number_normalized.ilike(f"%{digits_only}%"))
        query = query.filter(
            or_(
                *phone_filters,
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


def _contact_is_flagged_expr():
    def missing(column):
        return sa.or_(column.is_(None), sa.func.trim(column) == "", sa.func.lower(sa.func.trim(column)) == "unknown")
    dual = sa.and_(PremiumNumberContact.is_recruiter, PremiumNumberContact.is_employer, PremiumNumberContact.recruiter_verification_level == "unverified")
    recruiter = sa.and_(PremiumNumberContact.is_recruiter, sa.or_(dual, sa.and_(PremiumNumberContact.normalized_phone_number.ilike("nvoids-%"), sa.func.lower(PremiumNumberContact.display_phone_number) == "unknown", PremiumNumberContact.first_detected_email_id.is_(None)), missing(PremiumNumberContact.recruiter_name), missing(PremiumNumberContact.company)))
    employer = sa.and_(PremiumNumberContact.is_employer, sa.or_(dual, sa.and_(sa.not_(missing(PremiumNumberContact.display_phone_number)), PremiumNumberContact.phone_is_valid.is_(False)), missing(PremiumNumberContact.owner_name), missing(PremiumNumberContact.company)))
    return sa.or_(recruiter, employer)


@app.get("/premium-numbers/inventory", response_model=PremiumNumberInventoryListResponse)
def list_premium_number_inventory(
    cursor: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), q: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None), category: str | None = Query(default=None), source_type: str | None = Query(default=None),
    min_score: int | None = Query(default=None, ge=0), max_score: int | None = Query(default=None, ge=0), sort: str = Query("newest"),
    domain: str | None = Query(default=None, max_length=255), favorite: str = Query("all", pattern="^(all|favorites_only|non_favorites_only)$"),
    date_filter: str | None = Query(default=None), date_from: date | None = Query(default=None), date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PremiumNumberInventoryListResponse:
    if sort not in {"newest", "oldest", "highest_score", "lowest_score"}: raise HTTPException(status_code=422, detail="Invalid sort. Must be one of: newest, oldest, highest_score, lowest_score")
    if min_score is not None and max_score is not None and min_score > max_score: raise HTTPException(status_code=422, detail="min_score must be <= max_score")
    owner = settings.owner_id
    review = sa.select(NumberReviewQueue.id.label("id"), sa.literal("review").label("kind"), NumberReviewQueue.display_phone_number.label("number"), NumberReviewQueue.normalized_phone_number.label("normalized_number"), NumberReviewQueue.owner_name.label("owner"), NumberReviewQueue.company.label("company"), (NumberReviewQueue.role == "recruiter").label("is_recruiter"), (NumberReviewQueue.role == "employer").label("is_employer"), sa.literal("Pending").label("status"), NumberReviewQueue.recruiter_relevance_score.label("score"), sa.case((NumberReviewQueue.source_external_opportunity_id.is_not(None), "nvoids"), else_="gmail").label("source_type"), NumberReviewQueue.updated_at.label("last_checked_at")).where(NumberReviewQueue.owner_id == owner, NumberReviewQueue.state == "pending")
    active_score = sa.func.coalesce(sa.select(PremiumNumberLead.recruiter_relevance_score).where(PremiumNumberLead.id == PremiumNumberContact.active_recruiter_lead_id).scalar_subquery(), sa.select(PremiumNumberLead.recruiter_relevance_score).where(PremiumNumberLead.id == PremiumNumberContact.active_employer_lead_id).scalar_subquery())
    flagged = _contact_is_flagged_expr()
    contact_status = sa.case((PremiumNumberContact.normalized_phone_number.is_(None), "Unscored"), (flagged, "Flagged"), else_="Active")
    contact = sa.select(PremiumNumberContact.id.label("id"), sa.literal("contact").label("kind"), PremiumNumberContact.display_phone_number.label("number"), PremiumNumberContact.normalized_phone_number.label("normalized_number"), sa.func.coalesce(sa.case((PremiumNumberContact.is_recruiter, PremiumNumberContact.recruiter_name)), sa.case((PremiumNumberContact.is_employer, PremiumNumberContact.owner_name))).label("owner"), PremiumNumberContact.company.label("company"), PremiumNumberContact.is_recruiter.label("is_recruiter"), PremiumNumberContact.is_employer.label("is_employer"), contact_status.label("status"), active_score.label("score"), PremiumNumberContact.source_type.label("source_type"), PremiumNumberContact.updated_at.label("last_checked_at")).where(PremiumNumberContact.owner_id == owner, PremiumNumberContact.deleted_at.is_(None))
    if domain and domain.strip():
        needle = domain.strip()
        contact = contact.where(or_(PremiumNumberContact.recruiter_email_domain.ilike(f"%{needle}%"), PremiumNumberContact.employer_email_domain.ilike(f"%{needle}%"), PremiumNumberContact.id.in_(db.query(PremiumContactEmail.premium_contact_id).filter(PremiumContactEmail.domain.ilike(f"%{needle}%")))))
        # The review half of the union needs the same narrowing or every pending
        # row survives a domain filter and swamps the handful of matching
        # contacts - a review has no domain column, so match its own addresses.
        review = review.where(or_(NumberReviewQueue.contact_email.ilike(f"%{needle}%"), NumberReviewQueue.email_sender.ilike(f"%{needle}%")))
    if favorite == "favorites_only": contact = contact.where(PremiumNumberContact.is_favorite.is_(True)); review = review.where(sa.false())
    elif favorite == "non_favorites_only": contact = contact.where(PremiumNumberContact.is_favorite.is_(False)); review = review.where(sa.false())
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        contact = contact.where(PremiumNumberContact.created_at >= start, PremiumNumberContact.created_at < end)
        review = review.where(NumberReviewQueue.created_at >= start, NumberReviewQueue.created_at < end)
    unified = sa.union_all(review, contact).subquery()
    query = sa.select(unified)
    if q and q.strip():
        like=f"%{q.strip()}%"; digits_only=re.sub(r"\D","",q)
        alternate_phone_ilike = PremiumContactPhone.normalized_phone_number.ilike(f"%{digits_only}%") if digits_only else PremiumContactPhone.normalized_phone_number.ilike(like)
        alternate_ids = db.query(PremiumContactEmail.premium_contact_id).filter(PremiumContactEmail.normalized_email.ilike(like)).union(db.query(PremiumContactPhone.premium_contact_id).filter(alternate_phone_ilike))
        clauses=[unified.c.number.ilike(like),unified.c.owner.ilike(like),unified.c.company.ilike(like), sa.and_(unified.c.kind == "contact", unified.c.id.in_(alternate_ids))]
        if digits_only: clauses.append(unified.c.normalized_number.ilike(f"%{digits_only}%"))
        query=query.where(sa.or_(*clauses))
    if status:
        values=[value.strip().capitalize() for value in status.split(",") if value.strip()]
        if values: query=query.where(unified.c.status.in_(values))
    if category:
        values={value.strip().lower() for value in category.split(",")}; clauses=[]
        if "recruiter" in values: clauses.append(unified.c.is_recruiter.is_(True))
        if "employer" in values: clauses.append(unified.c.is_employer.is_(True))
        if clauses: query=query.where(sa.or_(*clauses))
    if source_type:
        values=[value.strip() for value in source_type.split(",") if value.strip()]
        if values: query=query.where(unified.c.source_type.in_(values))
    if min_score is not None: query=query.where(unified.c.score >= min_score)
    if max_score is not None: query=query.where(unified.c.score <= max_score)
    total=db.execute(sa.select(sa.func.count()).select_from(query.subquery())).scalar_one()
    order={"oldest":(unified.c.last_checked_at.asc(),unified.c.kind.asc(),unified.c.id.asc()),"highest_score":(sa.nullslast(unified.c.score.desc()),unified.c.kind.asc(),unified.c.id.asc()),"lowest_score":(sa.nullslast(unified.c.score.asc()),unified.c.kind.asc(),unified.c.id.asc())}.get(sort,(unified.c.last_checked_at.desc(),unified.c.kind.asc(),unified.c.id.asc()))
    rows=db.execute(query.order_by(*order).offset(cursor).limit(limit+1)).all(); visible=rows[:limit]
    review_ids=[row.id for row in visible if row.kind=="review"]; contact_ids=[row.id for row in visible if row.kind=="contact"]
    review_by_id={row.id:_review_card_response(db,row) for row in db.query(NumberReviewQueue).filter(NumberReviewQueue.owner_id==owner,NumberReviewQueue.id.in_(review_ids)).all()} if review_ids else {}
    contacts=db.query(PremiumNumberContact).filter(PremiumNumberContact.owner_id==owner,PremiumNumberContact.id.in_(contact_ids)).all() if contact_ids else []
    employer_domains=employer_domains_for_owner(db,owner) if any(row.is_recruiter for row in contacts) else set()
    recruiter_by_id={row.id:_recruiter_number_response(db,row,employer_domains) for row in contacts if row.is_recruiter}; employer_by_id={row.id:_employer_number_response(db,row) for row in contacts if row.is_employer}
    items=[PremiumNumberInventoryItemResponse(key=f"{row.kind}:{row.id}",kind=row.kind,id=row.id,number=row.number,owner=row.owner or "Unknown",company=row.company or "Unknown",categories=_contact_categories(row),status=row.status,score=row.score,sourceType=row.source_type if row.source_type in {"gmail","nvoids"} else None,lastCheckedAt=row.last_checked_at,review=review_by_id.get(row.id) if row.kind=="review" else None,recruiter=recruiter_by_id.get(row.id),employer=employer_by_id.get(row.id)) for row in visible]
    return PremiumNumberInventoryListResponse(items=items,next_cursor=cursor+limit if len(rows)>limit else None,has_next=len(rows)>limit,total=total)


# A company is keyed by the contact's email domain, because that is the only
# identity in this table an outside source can be joined back to. Contacts with
# no usable domain still need somewhere to live, so they fall back to grouping on
# the company name - the two never mix, since the company half of the key is
# blanked out whenever a domain exists.
_COMPANY_OPPORTUNITY_CAP = 25

CompanyGroup = tuple[str, str]


def _company_domain_expr():
    domain = sa.func.lower(sa.func.trim(sa.func.coalesce(
        sa.func.nullif(PremiumNumberContact.recruiter_email_domain, ""),
        sa.func.nullif(PremiumNumberContact.employer_email_domain, ""),
        sa.literal(""),
    )))
    # A free-mail address names a person, not an employer, so gmail.com must not
    # collect every unrelated recruiter who used one into a single "company".
    # Those contacts fall through to the company-name key instead.
    return sa.case((domain.in_(sorted(PERSONAL_EMAIL_DOMAINS)), sa.literal("")), else_=domain)


def _company_name_expr():
    domain = _company_domain_expr()
    company = sa.func.lower(sa.func.trim(sa.func.coalesce(PremiumNumberContact.company, sa.literal(""))))
    return sa.case((domain != "", sa.literal("")), else_=company)


def _company_group_of(contact: PremiumNumberContact) -> CompanyGroup:
    """The Python-side twin of the two SQL key expressions above."""
    domain = (contact.recruiter_email_domain or contact.employer_email_domain or "").strip().lower()
    if domain in PERSONAL_EMAIL_DOMAINS:
        domain = ""
    return (domain, "" if domain else str(contact.company or "").strip().lower())


def _company_display_name(contacts: list[PremiumNumberContact], fallback: str) -> str:
    counts: dict[str, int] = {}
    for contact in contacts:
        name = str(contact.company or "").strip()
        if not name or name.lower() == "unknown":
            continue
        counts[name] = counts.get(name, 0) + 1
    if not counts:
        return fallback
    # Ties (the same company spelled two ways across rows) resolve toward the
    # cased spelling, so a sloppy all-lowercase duplicate can't retitle the card.
    return max(counts.items(), key=lambda item: (item[1], item[0] != item[0].lower(), -len(item[0])))[0]


def _email_domain_match(column, domain: str):
    """Match an address column against one domain, portably.

    Postgres split_part and SQLite instr do not overlap, and the tests run on
    SQLite while production is Postgres, so the domain is matched by suffix
    instead of parsed out. The three shapes cover a bare address and the two
    ways a display-name header ends ("Name <a@b.com>", "Name <a@b.com> ").
    """
    return sa.or_(
        column.ilike(f"%@{domain}"),
        column.ilike(f"%@{domain}>%"),
        column.ilike(f"%@{domain} %"),
    )


def _company_application_ids(db: Session, owner: str, domains: list[str]) -> dict[str, list[int]]:
    """Applications belonging to each company domain.

    applications.recruiter_contact_id is the column this should join on, but it
    is NULL on every row, so the recruiter's own address is matched by domain
    instead - the same key the company cards are grouped by. Every application
    lookup in this file goes through here, so swapping this for the real foreign
    key once it is backfilled is a one-function change.
    """
    if not domains:
        return {}
    matched = sa.case(
        *[(_email_domain_match(Application.manual_recruiter_email, domain), sa.literal(domain)) for domain in domains],
        else_=sa.literal(""),
    )
    rows = db.execute(
        sa.select(matched.label("domain"), Application.id)
        .where(
            Application.owner_id == owner,
            Application.deleted_at.is_(None),
            sa.or_(*[_email_domain_match(Application.manual_recruiter_email, domain) for domain in domains]),
        )
    ).all()
    by_domain: dict[str, list[int]] = {}
    for row in rows:
        if row.domain:
            by_domain.setdefault(row.domain, []).append(row.id)
    return by_domain


def _company_opportunity_counts(db: Session, owner: str, contact_ids: list[int]) -> dict[int, int]:
    if not contact_ids:
        return {}
    rows = db.execute(
        sa.select(RecruiterOpportunity.recruiter_number_id, sa.func.count().label("total"))
        .where(RecruiterOpportunity.owner_id == owner, RecruiterOpportunity.recruiter_number_id.in_(contact_ids))
        .group_by(RecruiterOpportunity.recruiter_number_id)
    ).all()
    return {row.recruiter_number_id: row.total for row in rows}


def _company_conversation_stats(db: Session, owner: str, domains: list[str]) -> dict[str, dict[str, object]]:
    """Threads rooted in an email from this company, and how many answered back.

    email_conversations.status is 'replied' once the company wrote again after
    the user's send, which is the only direct "do they engage" signal in the
    schema - the application events that would otherwise carry it are unfilled.
    """
    if not domains:
        return {}
    matched = sa.case(
        *[(_email_domain_match(RecruiterEmail.sender, domain), sa.literal(domain)) for domain in domains],
        else_=sa.literal(""),
    )
    rows = db.execute(
        sa.select(
            matched.label("domain"),
            sa.func.count().label("conversations"),
            sa.func.sum(sa.case((EmailConversation.status == "replied", 1), else_=0)).label("replied"),
            sa.func.max(EmailConversation.last_message_at).label("last_reply_at"),
        )
        .select_from(EmailConversation)
        .join(RecruiterEmail, RecruiterEmail.id == EmailConversation.root_recruiter_email_id)
        .where(
            EmailConversation.owner_id == owner,
            sa.or_(*[_email_domain_match(RecruiterEmail.sender, domain) for domain in domains]),
        )
        .group_by(matched)
    ).all()
    return {
        row.domain: {"conversations": row.conversations, "replied": int(row.replied or 0), "last_reply_at": row.last_reply_at}
        for row in rows
        if row.domain
    }


def _company_inbound_email_stats(db: Session, owner: str, domains: list[str]) -> dict[str, dict[str, object]]:
    if not domains:
        return {}
    matched = sa.case(
        *[(_email_domain_match(RecruiterEmail.sender, domain), sa.literal(domain)) for domain in domains],
        else_=sa.literal(""),
    )
    rows = db.execute(
        sa.select(
            matched.label("domain"),
            sa.func.count().label("received"),
            sa.func.max(RecruiterEmail.gmail_received_at).label("last_inbound_at"),
        )
        .where(
            RecruiterEmail.owner_id == owner,
            sa.or_(*[_email_domain_match(RecruiterEmail.sender, domain) for domain in domains]),
        )
        .group_by(matched)
    ).all()
    return {row.domain: {"received": row.received, "last_inbound_at": row.last_inbound_at} for row in rows if row.domain}


def _company_inventory_item(
    db: Session,
    contact: PremiumNumberContact,
    employer_domains: set[str],
) -> PremiumNumberInventoryItemResponse:
    return PremiumNumberInventoryItemResponse(
        key=f"contact:{contact.id}",
        kind="contact",
        id=contact.id,
        number=contact.display_phone_number,
        owner=(contact.recruiter_name if contact.is_recruiter else contact.owner_name) or "Unknown",
        company=contact.company or "Unknown",
        categories=_contact_categories(contact),
        status="Unscored" if contact.normalized_phone_number is None else _contact_status(contact),
        score=_contact_source_fields(db, contact, "recruiter" if contact.is_recruiter else "employer")[5],
        sourceType=contact.source_type if contact.source_type in {"gmail", "nvoids"} else None,
        lastCheckedAt=contact.updated_at,
        recruiter=_recruiter_number_response(db, contact, employer_domains) if contact.is_recruiter else None,
        employer=_employer_number_response(db, contact) if contact.is_employer else None,
    )


def _company_cards(
    db: Session,
    owner: str,
    rows: list,
) -> tuple[list[PremiumCompanyCardResponse], dict[CompanyGroup, list[PremiumNumberContact]]]:
    """Build one card per grouped row, with every aggregate done in bulk.

    Four queries cover the whole page regardless of how many companies are on
    it, rather than one round trip per card.
    """
    groups: list[CompanyGroup] = [(row.domain, row.company) for row in rows]
    domain_expr, name_expr = _company_domain_expr(), _company_name_expr()
    members = sa.or_(*[sa.and_(domain_expr == domain, name_expr == company) for domain, company in groups])
    contacts = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner,
        PremiumNumberContact.deleted_at.is_(None),
        members,
    ).order_by(PremiumNumberContact.updated_at.desc(), PremiumNumberContact.id.desc()).all()

    by_group: dict[CompanyGroup, list[PremiumNumberContact]] = {}
    for contact in contacts:
        by_group.setdefault(_company_group_of(contact), []).append(contact)

    domains = [domain for domain, _company in groups if domain]
    opportunity_by_contact = _company_opportunity_counts(db, owner, [contact.id for contact in contacts])
    applications_by_domain = _company_application_ids(db, owner, domains)
    conversations_by_domain = _company_conversation_stats(db, owner, domains)

    cards: list[PremiumCompanyCardResponse] = []
    for row in rows:
        group = by_group.get((row.domain, row.company), [])
        statuses = [
            "Unscored" if contact.normalized_phone_number is None else _contact_status(contact)
            for contact in group
        ]
        conversation = conversations_by_domain.get(row.domain, {})
        cards.append(PremiumCompanyCardResponse(
            key=f"domain:{row.domain}" if row.domain else f"company:{row.company}",
            name=_company_display_name(group, row.domain or "Unknown company"),
            domain=row.domain,
            contact_count=row.contact_count,
            recruiter_count=sum(1 for contact in group if contact.is_recruiter),
            employer_count=sum(1 for contact in group if contact.is_employer),
            active_count=statuses.count("Active"),
            flagged_count=statuses.count("Flagged"),
            unscored_count=statuses.count("Unscored"),
            opportunity_count=sum(opportunity_by_contact.get(contact.id, 0) for contact in group),
            application_count=len(applications_by_domain.get(row.domain, [])),
            conversation_count=int(conversation.get("conversations", 0) or 0),
            replied_count=int(conversation.get("replied", 0) or 0),
            lastCheckedAt=row.last_checked_at,
        ))
    return cards, by_group


def _company_grouped_query(
    owner: str,
    q: str | None,
    date_filter: str | None,
    date_from: date | None,
    date_to: date | None,
):
    domain_expr, name_expr = _company_domain_expr(), _company_name_expr()
    base = sa.select(
        domain_expr.label("domain"),
        name_expr.label("company"),
        sa.func.count().label("contact_count"),
        sa.func.max(PremiumNumberContact.updated_at).label("last_checked_at"),
    ).where(PremiumNumberContact.owner_id == owner, PremiumNumberContact.deleted_at.is_(None))
    if q and q.strip():
        like = f"%{q.strip()}%"
        base = base.where(sa.or_(
            PremiumNumberContact.company.ilike(like),
            PremiumNumberContact.recruiter_email_domain.ilike(like),
            PremiumNumberContact.employer_email_domain.ilike(like),
        ))
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        base = base.where(PremiumNumberContact.created_at >= start, PremiumNumberContact.created_at < end)
    return base.group_by(domain_expr, name_expr)


_COMPANY_SORTS = {"newest", "oldest", "most_contacts", "fewest_contacts", "name", "most_opportunities", "most_applications", "most_replies"}
# The three activity sorts are applied after the page is built, because their
# values come from tables the grouped contact query never touches.
_COMPANY_POST_SORTS = {
    "most_opportunities": lambda card: card.opportunity_count,
    "most_applications": lambda card: card.application_count,
    "most_replies": lambda card: card.replied_count,
}


@app.get("/premium-numbers/companies", response_model=PremiumCompanyListResponse)
def list_premium_number_companies(
    cursor: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=50), q: str | None = Query(default=None, max_length=255),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None), date_from: date | None = Query(default=None), date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PremiumCompanyListResponse:
    if sort not in _COMPANY_SORTS:
        raise HTTPException(status_code=422, detail=f"Invalid sort. Must be one of: {', '.join(sorted(_COMPANY_SORTS))}")
    owner = settings.owner_id
    grouped = _company_grouped_query(owner, q, date_filter, date_from, date_to).subquery()
    total = db.execute(sa.select(sa.func.count()).select_from(grouped)).scalar_one()
    order = {
        "oldest": (grouped.c.last_checked_at.asc(),),
        "most_contacts": (grouped.c.contact_count.desc(), grouped.c.last_checked_at.desc()),
        "fewest_contacts": (grouped.c.contact_count.asc(), grouped.c.last_checked_at.desc()),
        "name": (grouped.c.domain.asc(), grouped.c.company.asc()),
    }.get(sort, (grouped.c.last_checked_at.desc(),))
    query = sa.select(grouped).order_by(*order, grouped.c.domain.asc(), grouped.c.company.asc())
    if sort in _COMPANY_POST_SORTS:
        # Ranking by activity has to see every company before it can pick a page,
        # so the whole grouped set is built and then sliced.
        rows = db.execute(query).all()
        cards, _members = _company_cards(db, owner, rows)
        cards.sort(key=_COMPANY_POST_SORTS[sort], reverse=True)
        page = cards[cursor:cursor + limit]
        has_next = cursor + limit < len(cards)
        return PremiumCompanyListResponse(items=page, next_cursor=cursor + limit if has_next else None, has_next=has_next, total=total)
    rows = db.execute(query.offset(cursor).limit(limit + 1)).all()
    visible = rows[:limit]
    if not visible:
        return PremiumCompanyListResponse(items=[], next_cursor=None, has_next=False, total=total)
    cards, _members = _company_cards(db, owner, visible)
    return PremiumCompanyListResponse(items=cards, next_cursor=cursor + limit if len(rows) > limit else None, has_next=len(rows) > limit, total=total)


def _tracked_count(value: int, tracked: bool) -> TrackedCount:
    return TrackedCount(value=value, tracked=tracked)


@app.get("/premium-numbers/companies/detail", response_model=PremiumCompanyDetailResponse)
def get_premium_number_company(
    domain: str = Query("", max_length=255),
    company: str = Query("", max_length=255),
    db: Session = Depends(get_db),
) -> PremiumCompanyDetailResponse:
    """One company card, its people, and what the relationship has produced.

    The group key is a domain or a company name, both of which carry dots and
    spaces, so it arrives as query parameters rather than a path segment.
    """
    domain, company = domain.strip().lower(), company.strip().lower()
    if not domain and not company:
        raise HTTPException(status_code=422, detail="Pass a domain or a company")
    if domain:
        company = ""
    owner = settings.owner_id
    grouped = _company_grouped_query(owner, None, None, None, None).subquery()
    row = db.execute(
        sa.select(grouped).where(grouped.c.domain == domain, grouped.c.company == company)
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Company not found")

    cards, members = _company_cards(db, owner, [row])
    card = cards[0]
    contacts = members.get((domain, company), [])
    employer_domains = employer_domains_for_owner(db, owner) if any(c.is_recruiter for c in contacts) else set()
    contact_ids = [contact.id for contact in contacts]

    opportunities = db.query(RecruiterOpportunity).filter(
        RecruiterOpportunity.owner_id == owner,
        RecruiterOpportunity.recruiter_number_id.in_(contact_ids) if contact_ids else sa.false(),
    ).order_by(RecruiterOpportunity.created_at.desc(), RecruiterOpportunity.id.desc()).limit(_COMPANY_OPPORTUNITY_CAP).all()

    application_ids = _company_application_ids(db, owner, [domain] if domain else []).get(domain, [])
    submissions = interviews = submitted_to_client = rtrs = 0
    if application_ids:
        submissions = db.query(Application).filter(
            Application.owner_id == owner,
            Application.id.in_(application_ids),
            Application.resume_submission_status == "submitted",
        ).count()
        submitted_to_client = db.query(Application).filter(
            Application.owner_id == owner,
            Application.id.in_(application_ids),
            Application.submitted_to_client_at.is_not(None),
        ).count()
        interviews = db.query(ApplicationInterview).filter(
            ApplicationInterview.owner_id == owner,
            ApplicationInterview.application_id.in_(application_ids),
            ApplicationInterview.deleted_at.is_(None),
        ).count()
        rtrs = db.query(ApplicationRTR).filter(
            ApplicationRTR.owner_id == owner,
            ApplicationRTR.application_id.in_(application_ids),
        ).count()

    # A stage nobody has ever recorded reads as "not tracked" everywhere, while a
    # stage that is in use but empty for this company is a real zero about them.
    # A company with no email domain cannot be attributed applications at all.
    attributable = bool(domain)
    interviews_exist = db.query(ApplicationInterview.id).filter(
        ApplicationInterview.owner_id == owner, ApplicationInterview.deleted_at.is_(None)
    ).first() is not None
    client_stage_exists = db.query(Application.id).filter(
        Application.owner_id == owner, Application.submitted_to_client_at.is_not(None)
    ).first() is not None
    rtr_exists = db.query(ApplicationRTR.id).filter(ApplicationRTR.owner_id == owner).first() is not None
    submissions_exist = db.query(Application.id).filter(
        Application.owner_id == owner, Application.resume_submission_status == "submitted"
    ).first() is not None

    inbound = _company_inbound_email_stats(db, owner, [domain] if domain else []).get(domain, {})
    conversation = _company_conversation_stats(db, owner, [domain] if domain else []).get(domain, {})
    conversations = int(conversation.get("conversations", 0) or 0)
    replied = int(conversation.get("replied", 0) or 0)

    return PremiumCompanyDetailResponse(
        company=card,
        contacts=[_company_inventory_item(db, contact, employer_domains) for contact in contacts],
        opportunities=[
            PremiumCompanyOpportunityResponse(
                id=row.id,
                job_title=row.job_title or "Untitled opportunity",
                end_client=row.end_client or "",
                status=row.status,
                created_at=row.created_at,
            )
            for row in opportunities
        ],
        pipeline=PremiumCompanyPipelineResponse(
            applications=_tracked_count(card.application_count, attributable),
            submissions=_tracked_count(submissions, attributable and submissions_exist),
            interviews=_tracked_count(interviews, attributable and interviews_exist),
            submitted_to_client=_tracked_count(submitted_to_client, attributable and client_stage_exists),
            rtrs=_tracked_count(rtrs, attributable and rtr_exists),
        ),
        responsiveness=PremiumCompanyResponsivenessResponse(
            emails_received=int(inbound.get("received", 0) or 0),
            conversations=conversations,
            replied=replied,
            reply_rate=round(replied / conversations, 3) if conversations else None,
            last_inbound_at=inbound.get("last_inbound_at"),
            last_reply_at=conversation.get("last_reply_at"),
        ),
    )


@app.get("/premium-numbers/deleted-contacts", response_model=PremiumNumberInventoryListResponse)
def list_deleted_premium_contacts(
    cursor: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), q: str | None = Query(default=None, max_length=255),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None), date_from: date | None = Query(default=None), date_to: date | None = Query(default=None),
    has_source_link: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> PremiumNumberInventoryListResponse:
    if sort not in {"newest", "oldest"}:
        raise HTTPException(status_code=422, detail="Invalid sort. Must be one of: newest, oldest")
    owner = settings.owner_id
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == owner,
        PremiumNumberContact.deleted_at.is_not(None),
    )
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        query = query.filter(PremiumNumberContact.deleted_at >= start, PremiumNumberContact.deleted_at < end)
    if has_source_link is not None:
        has_link = and_(PremiumNumberContact.source_link_url.is_not(None), PremiumNumberContact.source_link_url != "")
        query = query.filter(has_link if has_source_link else not_(has_link))
    if q and q.strip():
        like = f"%{q.strip()}%"
        digits_only = re.sub(r"\D", "", q)
        phone_filters = [PremiumNumberContact.display_phone_number.ilike(like)]
        if digits_only:
            phone_filters.append(PremiumNumberContact.normalized_phone_number.ilike(f"%{digits_only}%"))
        query = query.filter(
            or_(
                *phone_filters,
                PremiumNumberContact.recruiter_name.ilike(like),
                PremiumNumberContact.owner_name.ilike(like),
                PremiumNumberContact.company.ilike(like),
            )
        )
    total = query.count()
    order = PremiumNumberContact.deleted_at.asc() if sort == "oldest" else PremiumNumberContact.deleted_at.desc()
    rows = query.order_by(order, PremiumNumberContact.id.desc()).offset(cursor).limit(limit + 1).all()
    visible = rows[:limit]
    employer_domains = employer_domains_for_owner(db, owner) if any(row.is_recruiter for row in visible) else set()
    items = [
        PremiumNumberInventoryItemResponse(
            key=f"contact:{row.id}",
            kind="contact",
            id=row.id,
            number=row.display_phone_number,
            owner=(row.recruiter_name if row.is_recruiter else row.owner_name) or "Unknown",
            company=row.company or "Unknown",
            categories=_contact_categories(row),
            status="Unscored" if row.normalized_phone_number is None else _contact_status(row),
            score=_contact_source_fields(db, row, "recruiter" if row.is_recruiter else "employer")[5],
            sourceType=row.source_type if row.source_type in {"gmail", "nvoids"} else None,
            lastCheckedAt=row.deleted_at,
            recruiter=_recruiter_number_response(db, row, employer_domains) if row.is_recruiter else None,
            employer=_employer_number_response(db, row) if row.is_employer else None,
        )
        for row in visible
    ]
    return PremiumNumberInventoryListResponse(items=items, next_cursor=cursor + limit if len(rows) > limit else None, has_next=len(rows) > limit, total=total)


def _restore_contact_claims(db: Session, contact_id: int) -> list[str]:
    """Undelete and put back every identifier the soft delete had to give up. Returns the
    ones another contact has claimed since, which cannot be handed back."""
    contact = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_not(None),
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Deleted contact not found")
    # Cleared first: restore_contact_claims re-attaches through the same guards everyone
    # else uses, and those ignore rows that are still flagged deleted.
    contact.deleted_at = None
    db.flush()
    skipped = contact_identity_service.restore_contact_claims(db, contact)
    db.commit()
    return skipped


@app.post("/premium-numbers/contacts/{contact_id}/restore", response_model=dict[str, int | str])
def restore_premium_contact(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    skipped = _restore_contact_claims(db, contact_id)
    response: dict[str, int | str] = {"id": contact_id, "status": "restored"}
    if skipped:
        # Surfaced rather than swallowed: the contact is back but not whole, and the only
        # person who can decide what to do about it is looking at the Recycle Bin.
        response["unclaimed"] = ", ".join(skipped)
    return response


def _restore_contact(db: Session, contact_id: int) -> str:
    _restore_contact_claims(db, contact_id)
    return "restored"


@app.post("/premium-numbers/contacts/bulk-restore", response_model=BulkContactActionResponse)
def bulk_restore_premium_contacts(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(db, payload.contact_ids, _restore_contact)


def _purge_contact(db: Session, contact_id: int) -> str:
    # Only ever called on a contact that's already soft-deleted (Recycle Bin only) - this
    # is the one irreversible step, so it requires that prior confirmation to have happened.
    contact = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_not(None),
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Deleted contact not found")
    # premium_number_leads/premium_contact_emails/premium_contact_phones have real FK
    # constraints on this contact and must go first. recruiter_opportunities,
    # number_review_queue, and contact_identity_actions reference the id without a DB-level
    # FK (already handled as optional everywhere they're read, e.g.
    # _recruiter_opportunity_response's `recruiter: PremiumNumberContact | None`), so those
    # rows are left in place - they still carry their own historical value.
    contact.active_recruiter_lead_id = None
    contact.active_employer_lead_id = None
    db.query(PremiumNumberLead).filter(PremiumNumberLead.contact_id == contact_id).delete(synchronize_session=False)
    db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact_id).delete(synchronize_session=False)
    db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact_id).delete(synchronize_session=False)
    db.delete(contact)
    db.commit()
    return "purged"


@app.post("/premium-numbers/contacts/{contact_id}/purge", response_model=dict[str, int | str])
def purge_premium_contact(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    status = _purge_contact(db, contact_id)
    return {"id": contact_id, "status": status}


@app.post("/premium-numbers/contacts/bulk-purge", response_model=BulkContactActionResponse)
def bulk_purge_premium_contacts(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(db, payload.contact_ids, _purge_contact)


@app.post("/premium-numbers/contacts")
def create_premium_contact(
    payload: ManualPremiumContactRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    raw_phone = payload.phone.strip()
    canonical_phone = canonicalize_phone(raw_phone)
    if raw_phone and not canonical_phone:
        raise HTTPException(status_code=422, detail="Enter a valid US phone number")
    email = payload.email.strip().lower()
    if email and ("@" not in email or parseaddr(email)[1].lower() != email):
        raise HTTPException(status_code=422, detail="A valid email address is required")
    if not canonical_phone and not email:
        raise HTTPException(status_code=422, detail="Provide a phone number, an email address, or both")

    result = contact_identity_service.reconcile(
        db, owner_id=settings.owner_id, normalized_phone=canonical_phone, normalized_email=email,
        name=payload.name, company=payload.company, role=payload.role, human_confirmed=True,
    )
    if result.contact is None:
        if result.review_id is None:
            raise HTTPException(status_code=409, detail="Contact identity requires approval")
        # The phone and the email point at different people on file, so there is no one
        # contact to write this to. Report the queued review instead of erroring - and
        # above all do NOT fall back to whichever side matched first and stamp this
        # person's name, company and email onto it, which is how a headline email ended
        # up advertising an address a different contact owns.
        db.commit()
        return {
            "id": None,
            "created": False,
            "status": result.status,
            "review_id": result.review_id,
            "phone_display": "",
            "role": payload.role,
        }
    contact = result.contact
    created = result.status == "created"

    if canonical_phone:
        contact.display_phone_number = best_display_phone(raw_phone, fallback=raw_phone)
    contact.phone_is_valid = bool(canonicalize_phone(contact.normalized_phone_number or "") or canonicalize_phone(contact.display_phone_number))
    contact.is_recruiter = payload.role == "recruiter"
    contact.is_employer = payload.role == "employer"
    contact.recruiter_name = payload.name.strip() if payload.role == "recruiter" else contact.recruiter_name
    contact.owner_name = payload.name.strip() if payload.role == "employer" else contact.owner_name
    contact.designation = payload.title.strip() or "Unknown"
    contact.company = payload.company.strip() or "Unknown"
    if email:
        # Routed through attach_email rather than assigned: a bare headline write skips
        # the child table entirely, so it neither claims the address nor notices that
        # somebody else already owns it - and it always wrote recruiter_email even for an
        # employer. reconcile() has already linked it in the paths where it can.
        contact_identity_service.attach_email(
            db, contact, email, payload.role, None, primary=not bool(
                getattr(contact, contact_identity_service.headline_email_columns(payload.role)[0])
            ),
        )
    contact.source_type = "manual"
    contact.deleted_at = None
    db.commit()
    db.refresh(contact)
    # ponytail: event_source stays "chat_assistant" even for inventory-page creates
    # (test_chat_actions.py asserts on this label) - split it out if per-surface
    # attribution ever matters for analytics.
    _record_productivity_event(
        db,
        event_type="premium_contact_created",
        event_source="chat_assistant",
        entity_id=contact.id,
        entity_type="PremiumNumberContact",
        metadata={"role": payload.role, "created": created},
    )
    return {
        "id": contact.id,
        "created": created,
        "status": result.status,
        "review_id": result.review_id,
        "phone_display": contact.display_phone_number,
        "role": payload.role,
    }


@app.get("/premium-numbers/extraction-audit", response_model=ExtractionAuditListResponse)
def list_premium_number_extraction_audit(
    source_email_id: int | None = Query(default=None, ge=1),
    source_external_opportunity_id: int | None = Query(default=None, ge=1),
    status: str | None = Query(default=None, pattern=r"^(accepted|rejected)$"),
    db: Session = Depends(get_db),
) -> ExtractionAuditListResponse:
    if (source_email_id is None) == (source_external_opportunity_id is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one source_email_id or source_external_opportunity_id",
        )
    query = db.query(PremiumNumberExtractionAudit).filter(
        PremiumNumberExtractionAudit.owner_id == settings.owner_id
    )
    if source_email_id is not None:
        query = query.filter(PremiumNumberExtractionAudit.source_email_id == source_email_id)
    else:
        query = query.filter(
            PremiumNumberExtractionAudit.source_external_opportunity_id
            == source_external_opportunity_id
        )
    if status:
        query = query.filter(PremiumNumberExtractionAudit.status == status)
    rows = query.order_by(
        PremiumNumberExtractionAudit.created_at.asc(),
        PremiumNumberExtractionAudit.id.asc(),
    ).all()
    return ExtractionAuditListResponse(
        items=[ExtractionAuditEntryResponse.model_validate(row) for row in rows]
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


@app.get("/search/email", response_model=EmailSearchResponse)
def search_email(
    q: str = Query(..., min_length=1, max_length=255),
    section: str | None = Query(default=None, max_length=32),
    db: Session = Depends(get_db),
) -> EmailSearchResponse:
    normalized = q.strip()
    if len(normalized) < 2 and not normalized.isdecimal():
        raise HTTPException(status_code=422, detail="q must be a numeric Email ID or contain at least 2 characters")
    hits = email_lookup_service.search_email(db, owner_id=settings.owner_id, query=normalized, current_section=section)
    visible = hits[: email_lookup_service.MAX_EMAIL_SEARCH_HITS]
    return EmailSearchResponse(
        query=normalized,
        hits=[EmailSearchHitResponse(**hit.__dict__) for hit in visible],
        truncated=len(hits) > email_lookup_service.MAX_EMAIL_SEARCH_HITS,
    )


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
        digits_only = re.sub(r"\D", "", q)
        phone_filters = [NumberReviewQueue.display_phone_number.ilike(like)]
        if digits_only:
            phone_filters.append(NumberReviewQueue.normalized_phone_number.ilike(f"%{digits_only}%"))
        query = query.filter(
            or_(
                *phone_filters,
                NumberReviewQueue.owner_name.ilike(like),
                NumberReviewQueue.company.ilike(like),
                NumberReviewQueue.designation.ilike(like),
                NumberReviewQueue.contact_email.ilike(like),
                NumberReviewQueue.email_sender.ilike(like),
                NumberReviewQueue.email_subject.ilike(like),
            )
        )
    items = query.order_by(NumberReviewQueue.created_at.desc()).offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    next_cursor = cursor + limit if has_next else None
    return UnknownNumberReviewCardListResponse(
        items=[_review_card_response(db, row) for row in visible],
        next_cursor=next_cursor,
        has_next=has_next,
    )


@app.get("/number-review/pending-count", response_model=PendingNumberReviewCountResponse)
def pending_number_review_count(db: Session = Depends(get_db)) -> PendingNumberReviewCountResponse:
    count = (
        db.query(func.count(NumberReviewQueue.id))
        .filter(
            NumberReviewQueue.owner_id == settings.owner_id,
            NumberReviewQueue.state == "pending",
        )
        .scalar()
        or 0
    )
    return PendingNumberReviewCountResponse(count=int(count))


def _review_card(db: Session, review_id: int) -> NumberReviewQueue:
    card = (
        db.query(NumberReviewQueue)
        .filter(NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.id == review_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="Review card not found")
    return card


def _review_card_response(db: Session, row: NumberReviewQueue) -> UnknownNumberReviewCardResponse:
    response = UnknownNumberReviewCardResponse.model_validate(row)
    response.evidence_at = contact_identity_service._evidence_at(
        db, row.source_email_id, row.created_at, row.source_external_opportunity_id
    )
    return response


def _normalize_linkedin_url(value: str | None) -> str:
    normalized = (value or "").strip()
    if normalized and not normalized.lower().startswith(("http://", "https://")):
        return f"https://{normalized}"
    return normalized


def _parsed_review_phone(raw: str | None) -> tuple[str, str, str]:
    text = (raw or "").strip()
    if not text:
        return "", "", ""
    canonical, display, extension = format_phone(text)
    if not canonical:
        raise HTTPException(status_code=422, detail="Enter a valid US phone number")
    return canonical, display, extension


def _effective_review_values(
    card: NumberReviewQueue,
    submit: NumberReviewSubmitRequest | None,
) -> dict[str, str]:
    return {
        "owner_name": submit.owner_name if submit and submit.owner_name is not None else card.owner_name,
        "company": submit.company if submit and submit.company is not None else card.company,
        "display_phone_number": (
            submit.display_phone_number
            if submit and submit.display_phone_number is not None
            else card.display_phone_number
        ),
        "contact_email": (
            submit.contact_email
            if submit and submit.contact_email is not None
            else card.contact_email or extract_email_address(card.email_sender or "")
        ),
        "designation": (
            submit.designation if submit and submit.designation is not None else card.designation
        ),
    }


def _review_version(
    db: Session,
    card: NumberReviewQueue,
    values: dict[str, str],
    role: str,
    *,
    edited: bool,
) -> PremiumNumberLead:
    if not edited and card.source_lead_id:
        existing = (
            db.query(PremiumNumberLead)
            .filter(
                PremiumNumberLead.owner_id == settings.owner_id,
                PremiumNumberLead.id == card.source_lead_id,
            )
            .first()
        )
        if existing:
            return existing

    canonical_phone, display_phone, extension = _parsed_review_phone(values["display_phone_number"])
    version = PremiumNumberLead(
        owner_id=settings.owner_id,
        recruiter_email_id=card.source_email_id,
        external_opportunity_id=card.source_external_opportunity_id,
        phone_number_normalized=canonical_phone,
        phone_number_display=display_phone,
        phone_extension=extension,
        role=role,
        extraction_source="manual_review",
        contact_email=(values["contact_email"] or "").strip().lower(),
        owner_name=values["owner_name"] or "Unknown",
        company=values["company"] or "Unknown",
        designation=values["designation"] or "Unknown",
        purpose=card.purpose,
        confidence=card.confidence,
        contact_type=card.contact_type,
        recruiter_relevance_score=card.recruiter_relevance_score,
        is_recruiter_relevant=role == "recruiter",
        relevance_reason=card.relevance_reason,
        source_fragment=card.evidence_snippet,
        source_email_sender=card.email_sender,
        source_email_subject=card.email_subject,
        source_email_message_id=(
            f"nvoids:{card.source_external_opportunity_id}"
            if card.source_external_opportunity_id
            else f"manual-{card.source_email_id}"
        ),
        source_url=card.gmail_open_url or None,
    )
    db.add(version)
    db.flush()
    return version


def _review_fields_edited(
    card: NumberReviewQueue,
    submit: NumberReviewSubmitRequest | None,
) -> bool:
    if submit is None:
        return False
    return any(
        value is not None and value != (getattr(card, field) or "")
        for field, value in (
            ("owner_name", submit.owner_name),
            ("company", submit.company),
            ("display_phone_number", submit.display_phone_number),
            ("contact_email", submit.contact_email),
            ("designation", submit.designation),
        )
    )


def _contact_for_review(
    db: Session,
    card: NumberReviewQueue,
    values: dict[str, str],
) -> PremiumNumberContact:
    canonical_phone, _display, extension = _parsed_review_phone(values["display_phone_number"])
    candidate_email = (values.get("contact_email") or "").strip()
    if not canonical_phone and not candidate_email:
        raise HTTPException(status_code=422, detail="Review card has no phone number or email to identify a contact")
    resolution = contact_identity_service.resolve_identity(
        db,
        owner_id=settings.owner_id,
        phone=canonical_phone,
        extension=extension,
        email=candidate_email,
        role=card.role or "recruiter",
    )
    if resolution.outcome == "split":
        raise HTTPException(status_code=409, detail={
            "message": "Resolve the identity conflict on this card before marking it.",
            "target_contact_id": resolution.phone_owner.id if resolution.phone_owner else None,
            "secondary_contact_id": resolution.email_owner.id if resolution.email_owner else None,
        })
    result = contact_identity_service.reconcile(
        db,
        owner_id=settings.owner_id,
        normalized_phone=canonical_phone,
        normalized_email=candidate_email,
        name=values.get("owner_name", ""),
        company=values.get("company", ""),
        role=card.role or "recruiter",
        source_email_id=card.source_email_id,
        human_confirmed=True,
    )
    if result.contact is None:
        raise HTTPException(status_code=409, detail={
            "message": "Resolve the identity conflict on this card before marking it.",
            "target_contact_id": resolution.phone_owner.id if resolution.phone_owner else None,
            "secondary_contact_id": resolution.email_owner.id if resolution.email_owner else None,
        })
    result.contact.deleted_at = None
    if extension and not result.contact.phone_extension:
        result.contact.phone_extension = extension
    return result.contact


def _ensure_review_opportunity(
    db: Session,
    card: NumberReviewQueue,
    contact: PremiumNumberContact,
) -> None:
    email = None
    external = None
    if card.source_email_id:
        email = (
            db.query(RecruiterEmail)
            .filter(
                RecruiterEmail.owner_id == settings.owner_id,
                RecruiterEmail.id == card.source_email_id,
            )
            .first()
        )
    elif card.source_external_opportunity_id:
        external = (
            db.query(ExternalOpportunity)
            .filter(
                ExternalOpportunity.owner_id == settings.owner_id,
                ExternalOpportunity.id == card.source_external_opportunity_id,
            )
            .first()
        )
    gmail_message_id = (
        (email.external_message_id if email else None)
        or (f"nvoids:{external.external_post_id}" if external else None)
        or f"manual-{card.id}"
    )
    exists = (
        db.query(RecruiterOpportunity.id)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.recruiter_number_id == contact.id,
            RecruiterOpportunity.gmail_message_id == gmail_message_id,
        )
        .first()
    )
    if exists:
        return
    is_nvoids = external is not None
    row = RecruiterOpportunity(
        owner_id=settings.owner_id,
        recruiter_number_id=contact.id,
        source_email_id=card.source_email_id,
        gmail_message_id=gmail_message_id,
        source_type="nvoids" if is_nvoids else "gmail",
        source_url=(external.source_url if external else card.gmail_open_url) or None,
        external_opportunity_id=card.source_external_opportunity_id,
        email_subject=card.email_subject,
        email_sender=card.email_sender,
        gmail_open_url=card.gmail_open_url,
        received_at=(
            external.posted_at
            if external
            else (email.gmail_received_at if email else datetime.now(UTC))
        ),
        job_title=(external.role if external else (email.role if email else card.email_subject)),
        # `external.company` is the posting company, not the end client - the same
        # mapping defect fixed in phone_intelligence_workflow_service. An Nvoids
        # posting that never names a client leaves this blank: *not identified*.
        end_client=end_client_validation.clean_end_client(email.end_client if email else ""),
        location=(external.location if external else (email.location if email else "")),
        work_mode=external.work_mode if external else "",
        visa_restrictions=external.visa_hints if external else "",
        resume_file_name=(email.resume_file_name if email else "") or "",
        resume_asset_id=email.resume_asset_id if email else None,
        implementation_partner=(email.implementation_partner if email else "") or "",
        prime_vendor="",
        domain=(email.domain if email else "") or "",
        extracted_skills=(external.skills_text if external else (email.skills_text if email else "")),
        evidence=card.evidence_snippet,
        status="New",
        notes="",
    )
    db.add(row)
    db.flush()
    row.record_id = card.record_id
    if card.lineage_id:
        opportunity_lineage_service.attach_recruiter_opportunity(
            db,
            lineage_id=card.lineage_id,
            recruiter_opportunity_id=row.id,
        )
    else:
        source_type = "nvoids" if is_nvoids else "gmail"
        source_row_id = card.source_external_opportunity_id if is_nvoids else card.source_email_id
        lineage = opportunity_lineage_service.create_lineage(
            db,
            owner_id=settings.owner_id,
            origin_type=source_type,
            source_type=source_type,
            external_id=str(source_row_id) if source_row_id is not None else "",
            source_url=(external.source_url if external else card.gmail_open_url) or "",
            process_name="main_api",
            recruiter_opportunity_id=row.id,
        )
        card.lineage_id = lineage.id
        opportunity_lineage_service.link_record_to_lineage(
            db, record_id=card.record_id, lineage_id=lineage.id
        )


def _mark_number_as_recruiter(
    db: Session,
    review_id: int,
    submit: NumberReviewSubmitRequest | None = None,
) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    values = _effective_review_values(card, submit)
    candidate_email = extract_email_address(values["contact_email"])
    if not values["owner_name"] or values["owner_name"].strip().lower() == "unknown":
        values["owner_name"] = derive_name_from_contact_email(candidate_email) or "Unknown"
    if not values["company"] or values["company"].strip().lower() == "unknown":
        if is_derivable_company_domain(db, settings.owner_id, candidate_email):
            values["company"] = derive_company_from_email_domain(candidate_email) or "Unknown"
    values["contact_email"] = candidate_email
    contact = _contact_for_review(db, card, values)
    version = _review_version(
        db,
        card,
        values,
        "recruiter",
        edited=_review_fields_edited(card, submit),
    )
    version.contact_id = contact.id
    capture_sister_company(contact, values["company"])
    apply_contact_version(db, contact, version, "recruiter", overwrite=True)
    contact.first_detected_email_id = contact.first_detected_email_id or card.source_email_id
    if submit and submit.linkedin_url is not None:
        contact.linkedin_url = _normalize_linkedin_url(submit.linkedin_url)
    _ensure_review_opportunity(db, card, contact)
    card.state = "classified_recruiter"
    db.commit()
    return {"review_id": card.id, "status": card.state}


def _mark_number_as_employer(
    db: Session,
    review_id: int,
    submit: NumberReviewSubmitRequest | None = None,
) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    values = _effective_review_values(card, submit)
    contact = _contact_for_review(db, card, values)
    version = _review_version(
        db,
        card,
        values,
        "employer",
        edited=_review_fields_edited(card, submit),
    )
    version.contact_id = contact.id
    capture_sister_company(contact, values["company"])
    apply_contact_version(db, contact, version, "employer", overwrite=True)
    contact.source_email_id = contact.source_email_id or card.source_email_id
    if submit and submit.linkedin_url is not None:
        contact.linkedin_url = _normalize_linkedin_url(submit.linkedin_url)
    card.state = "classified_employer"
    db.commit()
    return {"review_id": card.id, "status": card.state}


def _delete_number_review_card(db: Session, review_id: int) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    card.state = "dismissed"
    db.commit()
    return {"review_id": card.id, "status": card.state}


def _bulk_review_response(
    db: Session,
    review_ids: list[int],
    action: Callable[[Session, int], dict[str, int | str]],
) -> BulkNumberReviewResponse:
    results: list[BulkNumberReviewResultItem] = []
    for review_id in review_ids:
        try:
            outcome = action(db, review_id)
            status = str(outcome["status"])
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            status = "not_found"
        results.append(BulkNumberReviewResultItem(review_id=review_id, status=status))
    return BulkNumberReviewResponse(results=results)


@app.post("/number-review/bulk-mark-recruiter", response_model=BulkNumberReviewResponse)
def bulk_mark_number_as_recruiter(
    payload: BulkNumberReviewRequest,
    db: Session = Depends(get_db),
) -> BulkNumberReviewResponse:
    return _bulk_review_response(db, payload.review_ids, _mark_number_as_recruiter)


@app.post("/number-review/bulk-mark-employer", response_model=BulkNumberReviewResponse)
def bulk_mark_number_as_employer(
    payload: BulkNumberReviewRequest,
    db: Session = Depends(get_db),
) -> BulkNumberReviewResponse:
    return _bulk_review_response(db, payload.review_ids, _mark_number_as_employer)


@app.post("/number-review/bulk-delete", response_model=BulkNumberReviewResponse)
def bulk_delete_number_review_cards(
    payload: BulkNumberReviewRequest,
    db: Session = Depends(get_db),
) -> BulkNumberReviewResponse:
    return _bulk_review_response(db, payload.review_ids, _delete_number_review_card)


@app.post("/number-review/bulk-rescore", response_model=BulkNumberReviewResponse)
def bulk_rescore_number_review_cards(
    payload: BulkNumberReviewRequest,
    db: Session = Depends(get_db),
) -> BulkNumberReviewResponse:
    rows = (
        db.query(NumberReviewQueue)
        .filter(
            NumberReviewQueue.owner_id == settings.owner_id,
            NumberReviewQueue.id.in_(payload.review_ids),
        )
        .all()
        if payload.review_ids
        else []
    )
    found = {row.id: row for row in rows}
    gmail_ids = {row.source_email_id for row in rows if row.state == "pending" and row.source_email_id}
    external_ids = {
        row.source_external_opportunity_id
        for row in rows
        if row.state == "pending" and row.source_external_opportunity_id
    }
    for email in (
        db.query(RecruiterEmail)
        .filter(
            RecruiterEmail.owner_id == settings.owner_id,
            RecruiterEmail.id.in_(gmail_ids),
        )
        .all()
        if gmail_ids
        else []
    ):
        _get_candidate_runtime_service().capture_premium_numbers(db, email)
    for item in (
        db.query(ExternalOpportunity)
        .filter(
            ExternalOpportunity.owner_id == settings.owner_id,
            ExternalOpportunity.id.in_(external_ids),
        )
        .all()
        if external_ids
        else []
    ):
        external_feed_service.phone_intelligence_workflow.capture_premium_numbers_for_nvoids(
            db,
            item,
            item.raw_body or "",
        )
    db.expire_all()
    refreshed = {
        row.id: row.state
        for row in db.query(NumberReviewQueue)
        .filter(
            NumberReviewQueue.owner_id == settings.owner_id,
            NumberReviewQueue.id.in_(payload.review_ids),
        )
        .all()
    } if payload.review_ids else {}
    return BulkNumberReviewResponse(
        results=[
            BulkNumberReviewResultItem(
                review_id=review_id,
                status=(
                    "not_found"
                    if review_id not in found
                    else refreshed.get(review_id, found[review_id].state)
                ),
            )
            for review_id in payload.review_ids
        ]
    )


@app.post("/number-review/{review_id}/mark-recruiter", response_model=dict[str, int | str])
def mark_number_as_recruiter(
    review_id: int,
    payload: NumberReviewSubmitRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    return _mark_number_as_recruiter(db, review_id, payload)


@app.post("/number-review/{review_id}/mark-employer", response_model=dict[str, int | str])
def mark_number_as_employer(
    review_id: int,
    payload: NumberReviewSubmitRequest | None = None,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    return _mark_number_as_employer(db, review_id, payload)


@app.post("/number-review/{review_id}/approve-link", response_model=dict[str, int | str])
def approve_contact_link(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    try:
        contact = contact_identity_service.approve_link(db, card)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # The reviewer just confirmed this is the same person under a phone/email that
    # disagreed with the contact on file - if the company disagreed too, that's not
    # something to silently drop now that identity is confirmed; keep it as a sister
    # company instead of losing it.
    capture_sister_company(contact, card.company)
    db.commit()
    return {"review_id": review_id, "contact_id": contact.id, "status": "resolved"}


@app.post("/number-review/{review_id}/approve-merge", response_model=dict[str, int | str])
def approve_contact_merge(review_id: int, payload: ContactMergeApprovalRequest | None = None, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    try:
        contact = contact_identity_service.approve_merge(db, card, payload.canonical_contact_id if payload else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return {"review_id": review_id, "contact_id": contact.id, "status": "resolved"}


def _contact_merge_preview_side(db: Session, contact_id: int) -> ContactMergePreviewSide:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == contact_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail=f"Contact {contact_id} not found")
    leads = (
        db.query(PremiumNumberLead)
        .filter(PremiumNumberLead.owner_id == settings.owner_id, PremiumNumberLead.contact_id == contact_id)
        .order_by(PremiumNumberLead.created_at.desc())
        .all()
    )
    latest_evidence_at = None
    for lead in leads:
        candidate = lead.created_at
        if lead.recruiter_email_id:
            email = db.get(RecruiterEmail, lead.recruiter_email_id)
            if email and email.gmail_received_at:
                candidate = email.gmail_received_at
        if latest_evidence_at is None or candidate > latest_evidence_at:
            latest_evidence_at = candidate
    phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).order_by(PremiumContactPhone.is_primary.desc(), PremiumContactPhone.id.asc()).all()
    emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).order_by(PremiumContactEmail.is_primary.desc(), PremiumContactEmail.id.asc()).all()
    return ContactMergePreviewSide(
        id=contact.id,
        recruiter_name=contact.recruiter_name,
        owner_name=contact.owner_name,
        company=contact.company,
        secondary_company=contact.secondary_company,
        recruiter_email=contact.recruiter_email,
        employer_email=contact.employer_email,
        normalized_phone_number=contact.normalized_phone_number,
        display_phone_number=contact.display_phone_number,
        phones=_phone_entries(contact, phones),
        emails=[{"email": row.normalized_email, "domain": row.domain, "is_primary": row.is_primary} for row in emails],
        is_recruiter=contact.is_recruiter,
        is_employer=contact.is_employer,
        lead_count=len(leads),
        latest_evidence_at=latest_evidence_at,
        leads=[
            ContactMergePreviewLead(
                id=lead.id,
                role=lead.role,
                company=lead.company,
                owner_name=lead.owner_name,
                contact_email=lead.contact_email,
                phone_number_display=lead.phone_number_display,
                extraction_source=lead.extraction_source,
                created_at=lead.created_at,
            )
            for lead in leads[:10]
        ],
    )


@app.get("/premium-numbers/contacts/merge-preview", response_model=ContactMergePreviewResponse)
def get_contact_merge_preview(
    contact_id_a: int, contact_id_b: int, db: Session = Depends(get_db)
) -> ContactMergePreviewResponse:
    return ContactMergePreviewResponse(
        contact_a=_contact_merge_preview_side(db, contact_id_a),
        contact_b=_contact_merge_preview_side(db, contact_id_b),
    )


@app.post("/premium-numbers/contacts/merge", response_model=ContactMergeResponse)
def merge_premium_contacts(payload: ContactMergeRequest, db: Session = Depends(get_db)) -> ContactMergeResponse:
    try:
        canonical = contact_identity_service.merge_contacts(
            db,
            owner_id=settings.owner_id,
            canonical_contact_id=payload.canonical_contact_id,
            loser_contact_id=payload.loser_contact_id,
            source="manual_merge_ui",
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return ContactMergeResponse(canonical_contact_id=canonical.id, loser_contact_id=payload.loser_contact_id, status="merged")


@app.post("/premium-numbers/contacts/backfill-duplicates", response_model=DuplicateContactBackfillResponse)
def backfill_duplicate_contacts(db: Session = Depends(get_db)) -> DuplicateContactBackfillResponse:
    # One-time cleanup for contacts that were already split across rows before the
    # extraction pipeline started merging same-email leads on its own (see
    # phone_intelligence_workflow_service's contact_enriched flow) - explicitly
    # human-triggered, so unlike that background flow this merges immediately instead of
    # queuing a review for each group.
    owner = settings.owner_id
    groups_merged = 0
    contacts_merged = 0
    emails = [
        row[0]
        for row in db.query(PremiumContactEmail.normalized_email)
        .filter(PremiumContactEmail.owner_id == owner)
        .distinct()
        .all()
    ]
    for email in emails:
        db.flush()
        child_ids = db.query(PremiumContactEmail.premium_contact_id).filter(
            PremiumContactEmail.owner_id == owner,
            PremiumContactEmail.normalized_email == email,
        )
        members = (
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == owner,
                PremiumNumberContact.deleted_at.is_(None),
                or_(
                    PremiumNumberContact.id.in_(child_ids),
                    PremiumNumberContact.recruiter_email == email,
                    PremiumNumberContact.employer_email == email,
                ),
            )
            .order_by(PremiumNumberContact.created_at.asc(), PremiumNumberContact.id.asc())
            .all()
        )
        if len(members) < 2:
            continue
        canonical, losers = members[0], members[1:]
        for loser in losers:
            canonical = contact_identity_service.merge_contacts(
                db, owner_id=owner, canonical_contact_id=canonical.id, loser_contact_id=loser.id,
                source="duplicate_backfill",
            )
            contacts_merged += 1
        groups_merged += 1
    db.commit()
    return DuplicateContactBackfillResponse(groups_merged=groups_merged, contacts_merged=contacts_merged)


@app.post("/number-review/{review_id}/dismiss", response_model=dict[str, int | str])
def dismiss_contact_suggestion(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    card = _review_card(db, review_id)
    # dismiss() SPLITS a new contact off the card, so replaying it on an already-settled
    # card mints a duplicate person per click. Every other review endpoint guards on this.
    if card.state != "pending":
        return {"review_id": card.id, "contact_id": card.target_contact_id or 0, "status": card.state}
    result = contact_identity_service.dismiss(db, card)
    db.commit()
    response: dict[str, int | str] = {
        "review_id": review_id, "contact_id": result.contact.id, "status": "dismissed",
    }
    if result.follow_up_review_id is not None:
        response["follow_up_review_id"] = result.follow_up_review_id
    return response


@app.post("/number-review/{review_id}/acknowledge", response_model=dict[str, int | str])
def acknowledge_contact_enrichment(review_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    # A "contact_enriched" review's change is already committed on the contact by the time
    # this fires - unlike /dismiss, there is nothing to create or undo here, just a record
    # that a human looked at what the AI merged.
    card = _review_card(db, review_id)
    # Guarded like every other review endpoint: without this, a pending identity_conflict
    # can be acknowledged straight to "settled" without the conflict ever being resolved.
    if card.state != "pending":
        return {"review_id": card.id, "status": card.state}
    card.state = "acknowledged"
    db.commit()
    return {"review_id": review_id, "status": "acknowledged"}


@app.delete("/number-review/{review_id}", response_model=dict[str, int | str])
def delete_number_review_card(
    review_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    return _delete_number_review_card(db, review_id)


@app.patch("/number-review/{review_id}", response_model=UnknownNumberReviewCardResponse)
def patch_number_review(
    review_id: int,
    payload: NumberReviewSubmitRequest,
    db: Session = Depends(get_db),
) -> UnknownNumberReviewCardResponse:
    card = _review_card(db, review_id)
    if card.state != "pending":
        raise HTTPException(status_code=409, detail="Only a pending review can be edited")
    if payload.owner_name is not None:
        card.owner_name = payload.owner_name.strip() or "Unknown"
    if payload.company is not None:
        card.company = payload.company.strip() or "Unknown"
    if payload.designation is not None:
        card.designation = payload.designation.strip() or "Unknown"
    if payload.contact_email is not None:
        email = payload.contact_email.strip().lower()
        if email and ("@" not in email or parseaddr(email)[1].lower() != email):
            raise HTTPException(status_code=422, detail="Enter a valid email address")
        card.contact_email = email
    if payload.linkedin_url is not None:
        card.linkedin_url = _normalize_linkedin_url(payload.linkedin_url)
    if payload.display_phone_number is not None:
        canonical, display, _extension = _parsed_review_phone(payload.display_phone_number)
        card.normalized_phone_number = canonical
        card.display_phone_number = display
    card.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(card)
    return _review_card_response(db, card)


def _phone_display(phone: str, extension: str) -> str:
    display = best_display_phone(phone, fallback=phone)
    return f"{display} ext {extension}" if extension else display


def _phone_entries(contact: PremiumNumberContact, rows: list[PremiumContactPhone]) -> list[dict[str, object]]:
    # is_primary is never trusted from the stored row - historical merges and identity
    # links have left it out of sync with the contact's own actual primary more than once.
    # The contact's own fields are the only reliable source of truth for which number is
    # primary, so the primary entry is always synthesized from them, and any row that
    # happens to duplicate it (by value) is skipped rather than shown twice.
    entries: list[dict[str, object]] = []
    primary_key = (contact.normalized_phone_number, contact.phone_extension or "")
    if contact.normalized_phone_number:
        entries.append({
            "phone": contact.normalized_phone_number,
            "extension": contact.phone_extension or "",
            "display": contact.display_phone_number or _phone_display(contact.normalized_phone_number, contact.phone_extension or ""),
            "is_primary": True,
            "is_verified": False,
            "label": "",
        })
    for row in rows:
        if (row.normalized_phone_number, row.phone_extension or "") == primary_key:
            continue
        entries.append({
            "phone": row.normalized_phone_number,
            "extension": row.phone_extension,
            "display": _phone_display(row.normalized_phone_number, row.phone_extension),
            "is_primary": False,
            "is_verified": row.is_verified,
            "label": row.label,
        })
    return entries


def _contact_source_fields(
    db: Session,
    contact: PremiumNumberContact,
    role: str,
) -> tuple[str | None, int | None, str | None, int | None, int, int | None]:
    active_id = (
        contact.active_recruiter_lead_id if role == "recruiter" else contact.active_employer_lead_id
    )
    versions = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.contact_id == contact.id,
    )
    count = versions.count()
    active = versions.filter(PremiumNumberLead.id == active_id).first() if active_id else None
    source_type = contact.source_type
    source_id = contact.source_id
    source_link = contact.source_link_url
    if not source_type and active and active.external_opportunity_id:
        source_type, source_id, source_link = "nvoids", active.external_opportunity_id, active.source_url
    elif not source_type and active and active.recruiter_email_id:
        source_type, source_id, source_link = "gmail", active.recruiter_email_id, active.source_url
    elif not source_type:
        source_id = contact.first_detected_email_id if role == "recruiter" else contact.source_email_id
        source_type = "gmail" if source_id else None
    score = active.recruiter_relevance_score if active else None
    return source_type, source_id, source_link, active_id, count, score


def _is_missing_value(value: str | None) -> bool:
    return not value or not value.strip() or value.strip().lower() == "unknown"


def _contact_is_flagged(contact: PremiumNumberContact, role: str) -> bool:
    if (
        contact.is_recruiter
        and contact.is_employer
        and contact.recruiter_verification_level == "unverified"
    ):
        return True
    if role == "recruiter":
        if is_hidden_nvoids_placeholder_recruiter(contact):
            return True
        name = contact.recruiter_name
    else:
        if is_hidden_invalid_employer_number(contact):
            return True
        name = contact.owner_name
    return _is_missing_value(name) or _is_missing_value(contact.company)


def _apply_recruiter_designation_fallback(contact: PremiumNumberContact, employer_domains: set[str]) -> None:
    if not _is_missing_value(contact.designation):
        return
    domain = email_domain(contact.recruiter_email or "")
    if domain and domain not in employer_domains:
        contact.designation = "Recruiter"


def _recruiter_number_response(
    db: Session, contact: PremiumNumberContact, employer_domains: set[str] | None = None
) -> RecruiterNumberResponse:
    _apply_recruiter_designation_fallback(
        contact, employer_domains if employer_domains is not None else employer_domains_for_owner(db, contact.owner_id)
    )
    total = (
        db.query(func.count(RecruiterOpportunity.id))
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.recruiter_number_id == contact.id,
        )
        .scalar()
        or 0
    )
    last_received = (
        db.query(func.max(RecruiterOpportunity.received_at))
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.recruiter_number_id == contact.id,
        )
        .scalar()
    )
    source_type, source_id, source_link, active_id, version_count, score = _contact_source_fields(
        db, contact, "recruiter"
    )
    flagged = _contact_is_flagged(contact, "recruiter")
    emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).order_by(PremiumContactEmail.is_primary.desc(), PremiumContactEmail.id.asc()).all()
    phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).order_by(PremiumContactPhone.is_primary.desc(), PremiumContactPhone.id.asc()).all()
    return RecruiterNumberResponse(
        id=contact.id,
        normalized_phone_number=contact.normalized_phone_number,
        display_phone_number=contact.display_phone_number,
        recruiter_name=contact.recruiter_name,
        company=contact.company,
        secondary_company=contact.secondary_company,
        designation=contact.designation,
        recruiter_email=contact.recruiter_email,
        recruiter_email_domain=contact.recruiter_email_domain,
        employer_email_domain=contact.employer_email_domain,
        is_favorite=contact.is_favorite,
        emails=[{"email": row.normalized_email, "domain": row.domain, "is_primary": row.is_primary} for row in emails],
        phones=_phone_entries(contact, phones),
        first_detected_email_id=contact.first_detected_email_id,
        source_type=source_type,
        source_id=source_id,
        source_link_url=source_link,
        active_lead_id=active_id,
        version_count=version_count,
        seen_count=contact.seen_count,
        linkedin_url=contact.linkedin_url,
        recruiter_verification_level=contact.recruiter_verification_level,
        do_not_work_again=contact.do_not_work_again,
        do_not_work_again_reason=contact.do_not_work_again_reason,
        total_opportunity_count=int(total),
        last_email_received_at=last_received,
        is_recruiter=contact.is_recruiter,
        is_employer=contact.is_employer,
        recruiter_relevance_score=score,
        status="Flagged" if flagged else "Active",
        flagged=flagged,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
    )


def _employer_number_response(db: Session, contact: PremiumNumberContact) -> EmployerNumberResponse:
    source_type, source_id, source_link, active_id, version_count, score = _contact_source_fields(
        db, contact, "employer"
    )
    flagged = _contact_is_flagged(contact, "employer")
    emails = db.query(PremiumContactEmail).filter(PremiumContactEmail.premium_contact_id == contact.id).order_by(PremiumContactEmail.is_primary.desc(), PremiumContactEmail.id.asc()).all()
    phones = db.query(PremiumContactPhone).filter(PremiumContactPhone.premium_contact_id == contact.id).order_by(PremiumContactPhone.is_primary.desc(), PremiumContactPhone.id.asc()).all()
    return EmployerNumberResponse(
        id=contact.id,
        normalized_phone_number=contact.normalized_phone_number,
        display_phone_number=contact.display_phone_number,
        owner_name=contact.owner_name,
        designation=contact.designation,
        company=contact.company,
        secondary_company=contact.secondary_company,
        employer_email=contact.employer_email,
        employer_email_domain=contact.employer_email_domain,
        is_favorite=contact.is_favorite,
        emails=[{"email": row.normalized_email, "domain": row.domain, "is_primary": row.is_primary} for row in emails],
        source_email_id=contact.source_email_id,
        source_type=source_type,
        source_id=source_id,
        source_link_url=source_link,
        active_lead_id=active_id,
        version_count=version_count,
        seen_count=contact.seen_count,
        phones=_phone_entries(contact, phones),
        linkedin_url=contact.linkedin_url,
        recruiter_verification_level=contact.recruiter_verification_level,
        do_not_work_again=contact.do_not_work_again,
        do_not_work_again_reason=contact.do_not_work_again_reason,
        is_recruiter=contact.is_recruiter,
        is_employer=contact.is_employer,
        recruiter_relevance_score=score,
        status="Flagged" if flagged else "Active",
        flagged=flagged,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
    )


@app.get("/recruiter-numbers", response_model=RecruiterNumberListResponse)
def list_recruiter_numbers(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(default=None),
    source_type: str | None = Query(default=None, pattern=r"^(gmail|nvoids)$"),
    flagged: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> RecruiterNumberListResponse:
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.is_recruiter.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    )
    if source_type:
        query = query.filter(PremiumNumberContact.source_type == source_type)
    if q:
        like = f"%{q.strip()}%"
        digits_only = re.sub(r"\D", "", q)
        phone_filters = [PremiumNumberContact.display_phone_number.ilike(like)]
        if digits_only:
            phone_filters.append(PremiumNumberContact.normalized_phone_number.ilike(f"%{digits_only}%"))
        query = query.filter(
            or_(
                *phone_filters,
                PremiumNumberContact.recruiter_name.ilike(like),
                PremiumNumberContact.company.ilike(like),
                PremiumNumberContact.designation.ilike(like),
                PremiumNumberContact.recruiter_email.ilike(like),
            )
        )
    rows = (
        query
        .order_by(PremiumNumberContact.updated_at.desc())
        .all()
    )
    employer_domains = employer_domains_for_owner(db, settings.owner_id)
    results = [
        _recruiter_number_response(db, row, employer_domains)
        for row in rows
        if (
            (not _contact_is_flagged(row, "recruiter"))
            if flagged is None
            else _contact_is_flagged(row, "recruiter") is flagged
        )
    ]
    visible, next_cursor, has_next = _paginate_items(results, cursor=cursor, limit=limit)
    return RecruiterNumberListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


def _apply_phone_list(db: Session, contact: PremiumNumberContact, raw_phones: list[str]) -> None:
    """Replaces a contact's entire phone set (primary + secondaries) from a full-editor
    save. The first entry becomes primary; the rest are recorded as secondaries. This is a
    full replace, not an incremental diff - simpler to reason about than tracking which row
    changed, and the editor always submits the complete list it's showing.
    """
    parsed: list[tuple[str, str, str]] = []
    for raw in raw_phones:
        raw = raw.strip()
        if not raw:
            continue
        canonical, display, extension = format_phone(raw)
        if not canonical:
            raise HTTPException(status_code=422, detail=f"'{raw}' is not a valid US phone number")
        key = (canonical, extension)
        if any((c, e) == key for c, _d, e in parsed):
            continue
        parsed.append((canonical, display, extension))
    for canonical, _display, extension in parsed:
        # Shared with contact_identity_service.add_phone so the editor and the ingestion
        # path cannot disagree about whether a number is already taken.
        conflicting_id = contact_identity_service.phone_claim_owner(
            db, contact.owner_id, canonical, extension, exclude_contact_id=contact.id
        )
        if conflicting_id is not None:
            raise HTTPException(
                status_code=409,
                detail={"message": f"{_display} is already linked to a different contact", "conflicting_contact_id": conflicting_id},
            )
    if parsed:
        primary_canonical, primary_display, primary_extension = parsed[0]
        contact.normalized_phone_number = primary_canonical
        contact.display_phone_number = primary_display
        contact.phone_extension = primary_extension
        contact.phone_is_valid = True
    else:
        contact.normalized_phone_number = None
        contact.display_phone_number = ""
        contact.phone_extension = ""
        contact.phone_is_valid = False
    # Scoped to unlabeled rows only - a fax/other row recorded by extraction isn't part of
    # this editor's list at all, and a blanket delete here would silently wipe it out on
    # every save of the regular phone numbers.
    db.query(PremiumContactPhone).filter(
        PremiumContactPhone.premium_contact_id == contact.id, PremiumContactPhone.label == "",
    ).delete(synchronize_session=False)
    for canonical, _display, extension in parsed[1:]:
        db.add(PremiumContactPhone(
            owner_id=contact.owner_id, premium_contact_id=contact.id, normalized_phone_number=canonical,
            phone_extension=extension, is_primary=False, is_verified=False, source="manual_edit", created_at=utc_now(),
        ))


def _apply_email_list(db: Session, contact: PremiumNumberContact, raw_emails: list[str], role: str) -> None:
    emails: list[str] = []
    for raw in raw_emails:
        email = raw.strip().lower()
        if not email:
            continue
        if "@" not in email or parseaddr(email)[1] != email:
            raise HTTPException(status_code=422, detail=f"'{raw}' is not a valid email address")
        if email not in emails:
            emails.append(email)
    for email in emails:
        owner = contact_identity_service.find_email_owner(db, contact.owner_id, email)
        if owner is not None and owner.id != contact.id:
            raise HTTPException(status_code=409, detail={
                "message": f"{email} is already linked to a different contact",
                "conflicting_contact_id": owner.id,
            })
    db.query(PremiumContactEmail).filter(
        PremiumContactEmail.premium_contact_id == contact.id,
        PremiumContactEmail.role == role,
    ).delete(synchronize_session=False)
    if not emails:
        contact_identity_service.set_headline_email(contact, "", role)
        return
    for index, email in enumerate(emails):
        contact_identity_service.attach_email(db, contact, email, role, None, primary=index == 0)


@app.patch("/recruiter-numbers/{recruiter_number_id}", response_model=RecruiterNumberResponse)
def patch_recruiter_number(
    recruiter_number_id: int,
    payload: RecruiterNumberPatchRequest,
    db: Session = Depends(get_db),
) -> RecruiterNumberResponse:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == recruiter_number_id,
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    if payload.recruiter_name is not None:
        contact.recruiter_name = payload.recruiter_name.strip() or "Unknown"
    if payload.company is not None:
        contact.company = payload.company.strip() or "Unknown"
    if payload.secondary_company is not None:
        contact.secondary_company = payload.secondary_company.strip()
    if payload.designation is not None:
        contact.designation = payload.designation.strip() or "Unknown"
    if payload.recruiter_email is not None:
        _apply_email_list(db, contact, [payload.recruiter_email], "recruiter")
    if payload.phone_number is not None:
        raw_phone = payload.phone_number.strip()
        if raw_phone:
            canonical, display, extension = format_phone(raw_phone)
            if not canonical:
                raise HTTPException(status_code=422, detail="Enter a valid US phone number")
            contact.normalized_phone_number = canonical
            contact.display_phone_number = display
            contact.phone_extension = extension
            contact.phone_is_valid = True
        else:
            contact.normalized_phone_number = None
            contact.display_phone_number = ""
            contact.phone_extension = ""
            contact.phone_is_valid = False
    if payload.phones is not None:
        _apply_phone_list(db, contact, payload.phones)
    if payload.emails is not None:
        _apply_email_list(db, contact, payload.emails, "recruiter")
    if payload.is_favorite is not None:
        contact.is_favorite = payload.is_favorite
    if payload.linkedin_url is not None:
        contact.linkedin_url = _normalize_linkedin_url(payload.linkedin_url)
    if payload.recruiter_verification_level is not None:
        contact.recruiter_verification_level = payload.recruiter_verification_level
    if payload.do_not_work_again is not None:
        contact.do_not_work_again = payload.do_not_work_again
    if payload.do_not_work_again_reason is not None:
        contact.do_not_work_again_reason = payload.do_not_work_again_reason
    contact.updated_at = datetime.now(UTC)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another contact already uses this exact phone number and extension") from None
    db.refresh(contact)
    return _recruiter_number_response(db, contact)


@app.patch("/employer-numbers/{employer_number_id}", response_model=EmployerNumberResponse)
def patch_employer_number(
    employer_number_id: int,
    payload: EmployerNumberPatchRequest,
    db: Session = Depends(get_db),
) -> EmployerNumberResponse:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == employer_number_id,
            PremiumNumberContact.is_employer.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Employer number not found")
    if payload.owner_name is not None:
        contact.owner_name = payload.owner_name.strip() or "Unknown"
    if payload.designation is not None:
        contact.designation = payload.designation.strip() or "Unknown"
    if payload.company is not None:
        contact.company = payload.company.strip() or "Unknown"
    if payload.secondary_company is not None:
        contact.secondary_company = payload.secondary_company.strip()
    if payload.employer_email is not None:
        _apply_email_list(db, contact, [payload.employer_email], "employer")
    if payload.phones is not None:
        _apply_phone_list(db, contact, payload.phones)
    if payload.emails is not None:
        _apply_email_list(db, contact, payload.emails, "employer")
    if payload.is_favorite is not None:
        contact.is_favorite = payload.is_favorite
    if payload.linkedin_url is not None:
        contact.linkedin_url = _normalize_linkedin_url(payload.linkedin_url)
    if payload.recruiter_verification_level is not None:
        contact.recruiter_verification_level = payload.recruiter_verification_level
    if payload.do_not_work_again is not None:
        contact.do_not_work_again = payload.do_not_work_again
    if payload.do_not_work_again_reason is not None:
        contact.do_not_work_again_reason = payload.do_not_work_again_reason
    contact.updated_at = datetime.now(UTC)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Another contact already uses this exact phone number and extension") from None
    db.refresh(contact)
    return _employer_number_response(db, contact)


@app.get("/recruiter-numbers/{recruiter_number_id}", response_model=RecruiterNumberResponse)
def get_recruiter_number(
    recruiter_number_id: int,
    db: Session = Depends(get_db),
) -> RecruiterNumberResponse:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == recruiter_number_id,
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    return _recruiter_number_response(db, contact)


@app.get("/employer-numbers/{employer_number_id}", response_model=EmployerNumberResponse)
def get_employer_number(
    employer_number_id: int,
    db: Session = Depends(get_db),
) -> EmployerNumberResponse:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == employer_number_id,
            PremiumNumberContact.is_employer.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Employer number not found")
    return _employer_number_response(db, contact)


@app.get("/recruiter-numbers/{contact_id}/versions", response_model=list[PremiumNumberResponse])
def list_recruiter_number_versions(
    contact_id: int,
    db: Session = Depends(get_db),
) -> list[PremiumNumberResponse]:
    contact = db.query(PremiumNumberContact.id).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.is_recruiter.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    rows = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.contact_id == contact_id,
    ).order_by(PremiumNumberLead.created_at.desc(), PremiumNumberLead.id.desc()).all()
    return [PremiumNumberResponse.model_validate(row) for row in rows]


def _select_contact_version(
    db: Session,
    contact_id: int,
    lead_id: int,
    role: str,
) -> PremiumNumberContact:
    contact_query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_(None),
    )
    contact_query = contact_query.filter(
        PremiumNumberContact.is_recruiter.is_(True)
        if role == "recruiter"
        else PremiumNumberContact.is_employer.is_(True)
    )
    contact = contact_query.first()
    lead = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.id == lead_id,
        PremiumNumberLead.contact_id == contact_id,
    ).first()
    if not contact or not lead:
        raise HTTPException(status_code=404, detail="Contact version not found")
    apply_contact_version(db, contact, lead, role, overwrite=True)
    contact.updated_at = datetime.now(UTC)
    db.commit()
    return contact


def _add_employer_role(db: Session, contact: PremiumNumberContact) -> None:
    contact.display_phone_number = _standardized_display_phone(
        contact.display_phone_number,
        contact.normalized_phone_number,
        fallback=contact.display_phone_number,
    )
    contact.is_employer = True
    contact.owner_name = contact.recruiter_name or contact.owner_name or "Unknown"
    contact.source_email_id = contact.source_email_id or contact.first_detected_email_id
    if contact.active_employer_lead_id is None:
        contact.active_employer_lead_id = contact.active_recruiter_lead_id
    if contact.active_employer_lead_id:
        lead = db.get(PremiumNumberLead, contact.active_employer_lead_id)
        if lead:
            apply_contact_version(db, contact, lead, "employer", overwrite=True)
    contact.phone_is_valid = bool(canonicalize_phone(contact.normalized_phone_number) or canonicalize_phone(contact.display_phone_number))
    contact.updated_at = datetime.now(UTC)


def _add_recruiter_role(db: Session, contact: PremiumNumberContact) -> None:
    contact.display_phone_number = _standardized_display_phone(
        contact.display_phone_number,
        contact.normalized_phone_number,
        fallback=contact.display_phone_number,
    )
    contact.is_recruiter = True
    contact.recruiter_name = contact.owner_name or contact.recruiter_name or "Unknown"
    contact.first_detected_email_id = contact.first_detected_email_id or contact.source_email_id
    if contact.active_recruiter_lead_id is None:
        contact.active_recruiter_lead_id = contact.active_employer_lead_id
    if contact.active_recruiter_lead_id:
        lead = db.get(PremiumNumberLead, contact.active_recruiter_lead_id)
        if lead:
            apply_contact_version(db, contact, lead, "recruiter", overwrite=True)
    contact.phone_is_valid = bool(canonicalize_phone(contact.normalized_phone_number) or canonicalize_phone(contact.display_phone_number))
    contact.updated_at = datetime.now(UTC)


def _remove_contact_role(db: Session, contact_id: int, role: str) -> str:
    contact = _contact_for_bulk_action(db, contact_id, role)
    other_role_present = contact.is_employer if role == "recruiter" else contact.is_recruiter
    if not other_role_present:
        raise HTTPException(status_code=422, detail="Cannot remove a contact's only role")
    if role == "recruiter":
        contact.is_recruiter = False
    else:
        contact.is_employer = False
    contact.updated_at = datetime.now(UTC)
    db.commit()
    return f"unmarked_{role}"


@app.post("/recruiter-numbers/{contact_id}/unmark", response_model=dict[str, int | str])
def unmark_recruiter_number(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    status = _remove_contact_role(db, contact_id, "recruiter")
    return {"id": contact_id, "status": status}


@app.post("/employer-numbers/{contact_id}/unmark", response_model=dict[str, int | str])
def unmark_employer_number(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    status = _remove_contact_role(db, contact_id, "employer")
    return {"id": contact_id, "status": status}


@app.post("/recruiter-numbers/{contact_id}/select-version/{lead_id}", response_model=dict[str, int | str])
def select_recruiter_number_version(
    contact_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    _select_contact_version(db, contact_id, lead_id, "recruiter")
    return {"id": contact_id, "active_lead_id": lead_id, "status": "selected"}


@app.post("/recruiter-numbers/{recruiter_number_id}/swap-to-employer", response_model=dict[str, int | str])
def swap_recruiter_number_to_employer(
    recruiter_number_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == recruiter_number_id,
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    _add_employer_role(db, contact)
    db.commit()
    return {"id": recruiter_number_id, "swapped_to": "employer"}


@app.get("/employer-numbers", response_model=EmployerNumberListResponse)
def list_employer_numbers(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    q: str | None = Query(default=None),
    source_type: str | None = Query(default=None, pattern=r"^(gmail|nvoids)$"),
    flagged: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EmployerNumberListResponse:
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.is_employer.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    )
    if source_type:
        query = query.filter(PremiumNumberContact.source_type == source_type)
    if q:
        like = f"%{q.strip()}%"
        digits_only = re.sub(r"\D", "", q)
        phone_filters = [PremiumNumberContact.display_phone_number.ilike(like)]
        if digits_only:
            phone_filters.append(PremiumNumberContact.normalized_phone_number.ilike(f"%{digits_only}%"))
        query = query.filter(
            or_(
                *phone_filters,
                PremiumNumberContact.owner_name.ilike(like),
                PremiumNumberContact.company.ilike(like),
            )
        )
    rows = (
        query
        .order_by(PremiumNumberContact.updated_at.desc())
        .all()
    )
    items = [
        _employer_number_response(db, row)
        for row in rows
        if (
            (not _contact_is_flagged(row, "employer"))
            if flagged is None
            else _contact_is_flagged(row, "employer") is flagged
        )
    ]
    visible, next_cursor, has_next = _paginate_items(items, cursor=cursor, limit=limit)
    return EmployerNumberListResponse(items=visible, next_cursor=next_cursor, has_next=has_next)


@app.post("/employer-numbers/{employer_number_id}/swap-to-recruiter", response_model=dict[str, int | str])
def swap_employer_number_to_recruiter(employer_number_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    contact = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == employer_number_id,
            PremiumNumberContact.is_employer.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Employer number not found")
    _add_recruiter_role(db, contact)
    db.commit()
    return {"id": employer_number_id, "swapped_to": "recruiter"}


@app.get("/employer-numbers/{contact_id}/versions", response_model=list[PremiumNumberResponse])
def list_employer_number_versions(
    contact_id: int,
    db: Session = Depends(get_db),
) -> list[PremiumNumberResponse]:
    contact = db.query(PremiumNumberContact.id).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.is_employer.is_(True),
        PremiumNumberContact.deleted_at.is_(None),
    ).first()
    if not contact:
        raise HTTPException(status_code=404, detail="Employer number not found")
    rows = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.contact_id == contact_id,
    ).order_by(PremiumNumberLead.created_at.desc(), PremiumNumberLead.id.desc()).all()
    return [PremiumNumberResponse.model_validate(row) for row in rows]


@app.post("/employer-numbers/{contact_id}/select-version/{lead_id}", response_model=dict[str, int | str])
def select_employer_number_version(
    contact_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    _select_contact_version(db, contact_id, lead_id, "employer")
    return {"id": contact_id, "active_lead_id": lead_id, "status": "selected"}


def _delete_contact_version(db: Session, contact_id: int, lead_id: int, role: str) -> None:
    contact_query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_(None),
    )
    contact_query = contact_query.filter(
        PremiumNumberContact.is_recruiter.is_(True)
        if role == "recruiter"
        else PremiumNumberContact.is_employer.is_(True)
    )
    contact = contact_query.first()
    lead = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.id == lead_id,
        PremiumNumberLead.contact_id == contact_id,
    ).first()
    if not contact or not lead:
        raise HTTPException(status_code=404, detail="Contact version not found")
    remaining = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.contact_id == contact_id,
        PremiumNumberLead.id != lead_id,
    ).order_by(PremiumNumberLead.created_at.desc(), PremiumNumberLead.id.desc()).all()
    if not remaining:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the only saved version. Delete the entire contact instead.",
        )
    replacement = remaining[0]
    if contact.active_recruiter_lead_id == lead_id:
        apply_contact_version(db, contact, replacement, "recruiter", overwrite=True)
    if contact.active_employer_lead_id == lead_id:
        apply_contact_version(db, contact, replacement, "employer", overwrite=True)
    contact.updated_at = datetime.now(UTC)
    db.delete(lead)
    db.commit()


@app.delete("/recruiter-numbers/{contact_id}/versions/{lead_id}", response_model=dict[str, int | str])
def delete_recruiter_number_version(
    contact_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    _delete_contact_version(db, contact_id, lead_id, "recruiter")
    return {"id": contact_id, "deleted_lead_id": lead_id, "status": "version_deleted"}


@app.delete("/employer-numbers/{contact_id}/versions/{lead_id}", response_model=dict[str, int | str])
def delete_employer_number_version(
    contact_id: int,
    lead_id: int,
    db: Session = Depends(get_db),
) -> dict[str, int | str]:
    _delete_contact_version(db, contact_id, lead_id, "employer")
    return {"id": contact_id, "deleted_lead_id": lead_id, "status": "version_deleted"}


def _delete_older_contact_versions(db: Session, contact_id: int, role: str) -> int:
    contact = _contact_for_bulk_action(db, contact_id, role)
    keep_ids = {contact.active_recruiter_lead_id, contact.active_employer_lead_id} - {None}
    query = db.query(PremiumNumberLead).filter(
        PremiumNumberLead.owner_id == settings.owner_id,
        PremiumNumberLead.contact_id == contact_id,
    )
    if keep_ids:
        query = query.filter(PremiumNumberLead.id.notin_(keep_ids))
    count = query.delete(synchronize_session=False)
    db.commit()
    return count


@app.delete("/recruiter-numbers/{contact_id}/versions", response_model=dict[str, int | str])
def delete_older_recruiter_number_versions(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    count = _delete_older_contact_versions(db, contact_id, "recruiter")
    return {"id": contact_id, "deleted_count": count, "status": "versions_pruned"}


@app.delete("/employer-numbers/{contact_id}/versions", response_model=dict[str, int | str])
def delete_older_employer_number_versions(contact_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    count = _delete_older_contact_versions(db, contact_id, "employer")
    return {"id": contact_id, "deleted_count": count, "status": "versions_pruned"}


def _contact_for_bulk_action(db: Session, contact_id: int, role: str) -> PremiumNumberContact:
    query = db.query(PremiumNumberContact).filter(
        PremiumNumberContact.owner_id == settings.owner_id,
        PremiumNumberContact.id == contact_id,
        PremiumNumberContact.deleted_at.is_(None),
    )
    query = query.filter(
        PremiumNumberContact.is_recruiter.is_(True)
        if role == "recruiter"
        else PremiumNumberContact.is_employer.is_(True)
    )
    contact = query.first()
    if not contact:
        raise HTTPException(status_code=404, detail="Premium number contact not found")
    return contact


def _bulk_contact_response(
    db: Session,
    contact_ids: list[int],
    action: Callable[[Session, int], str],
) -> BulkContactActionResponse:
    results: list[BulkContactActionResultItem] = []
    for contact_id in contact_ids:
        try:
            status = action(db, contact_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            status = "not_found"
        results.append(BulkContactActionResultItem(contact_id=contact_id, status=status))
    return BulkContactActionResponse(results=results)


def _soft_delete_contact(db: Session, contact_id: int, role: str) -> str:
    contact = _contact_for_bulk_action(db, contact_id, role)
    # Snapshot BEFORE releasing: release_contact_claims hard-deletes every phone and email
    # row, so without this a restore from the Recycle Bin brings the contact back with no
    # identifiers at all.
    contact_identity_service.snapshot_contact_claims(db, contact)
    contact_identity_service.release_contact_claims(db, contact)
    contact.deleted_at = datetime.now(UTC)
    # A pending review targeting this contact has nothing left to resolve against
    # once it's gone - leaving it "pending" strands it in Needs Review forever,
    # showing an unlabeled "existing contact" with no way to see who it was.
    db.query(NumberReviewQueue).filter(
        NumberReviewQueue.owner_id == settings.owner_id,
        NumberReviewQueue.state == "pending",
        or_(
            NumberReviewQueue.target_contact_id == contact_id,
            NumberReviewQueue.secondary_contact_id == contact_id,
        ),
    ).update({"state": "dismissed"}, synchronize_session=False)
    db.commit()
    return "deleted"


def _bulk_add_contact_role(db: Session, contact_id: int, role: str) -> str:
    source_role = "employer" if role == "recruiter" else "recruiter"
    contact = _contact_for_bulk_action(db, contact_id, source_role)
    if role == "recruiter":
        _add_recruiter_role(db, contact)
    else:
        _add_employer_role(db, contact)
    db.commit()
    return f"marked_{role}"


def _rescore_contact(db: Session, contact_id: int, role: str) -> str:
    contact = _contact_for_bulk_action(db, contact_id, role)
    source_type, source_id, _, _, _, _ = _contact_source_fields(db, contact, role)
    if source_type == "gmail" and source_id:
        source = db.query(RecruiterEmail).filter(
            RecruiterEmail.owner_id == settings.owner_id,
            RecruiterEmail.id == source_id,
        ).first()
        if not source:
            return "source_not_found"
        _get_candidate_runtime_service().capture_premium_numbers(db, source)
    elif source_type == "nvoids" and source_id:
        source = db.query(ExternalOpportunity).filter(
            ExternalOpportunity.owner_id == settings.owner_id,
            ExternalOpportunity.id == source_id,
        ).first()
        if not source:
            return "source_not_found"
        external_feed_service.phone_intelligence_workflow.capture_premium_numbers_for_nvoids(
            db,
            source,
            source.raw_body or "",
        )
    else:
        return "source_not_found"
    db.expire_all()
    refreshed = _contact_for_bulk_action(db, contact_id, role)
    active_id = (
        refreshed.active_recruiter_lead_id
        if role == "recruiter"
        else refreshed.active_employer_lead_id
    )
    active = db.get(PremiumNumberLead, active_id) if active_id else None
    if active:
        # overwrite=False: Rescore's job is to fill in what's missing and correct the
        # phone (below), not to relabel the contact as a different person if this same
        # source email also mentions someone else - name/email/company only fill blanks.
        apply_contact_version(db, refreshed, active, role, overwrite=False)
        if active.phone_number_normalized and active.phone_number_normalized != refreshed.normalized_phone_number:
            # apply_contact_version deliberately never touches the phone - it's the
            # contact's identity anchor, so correcting it has to go through the same
            # conflict check as any other phone link, not a blind overwrite.
            if not contact_identity_service.add_phone(db, refreshed, active.phone_number_normalized, primary=True):
                conflict_contact = contact_identity_service.find_phone_owner(db, refreshed.owner_id, active.phone_number_normalized)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"{active.phone_number_display or active.phone_number_normalized} is already linked to a different contact",
                        "conflicting_contact_id": conflict_contact.id if conflict_contact else None,
                    },
                )
        elif active.phone_extension and active.phone_extension != refreshed.phone_extension:
            # Same base number, so add_phone above never runs - but the re-extracted
            # extension is still a correction worth keeping, not silently dropping.
            refreshed.phone_extension = active.phone_extension
            refreshed.display_phone_number = f"{best_display_phone(refreshed.normalized_phone_number or '', fallback=refreshed.display_phone_number)} ext {active.phone_extension}"
    refreshed.updated_at = datetime.now(UTC)
    db.commit()
    return "rescored"


_RESCORE_TRACKED_FIELDS: dict[str, list[tuple[str, str]]] = {
    "recruiter": [
        ("recruiter_name", "Name"),
        ("company", "Company"),
        ("designation", "Designation"),
        ("recruiter_email", "Email"),
        ("linkedin_url", "LinkedIn"),
        ("display_phone_number", "Phone"),
    ],
    "employer": [
        ("owner_name", "Name"),
        ("company", "Company"),
        ("designation", "Designation"),
        ("employer_email", "Email"),
        ("linkedin_url", "LinkedIn"),
        ("display_phone_number", "Phone"),
    ],
}


def _rescore_contact_with_diff(db: Session, contact_id: int, role: str) -> ContactRescoreResponse:
    # Rescore's re-extraction is a real AI/pipeline run, not a read-only query, so it can't
    # be previewed without committing - it self-commits deep inside capture_premium_numbers.
    # Show the user what changed instead: snapshot before, run the real rescore, diff after.
    tracked = _RESCORE_TRACKED_FIELDS[role]
    before = _contact_for_bulk_action(db, contact_id, role)
    snapshot = {field: getattr(before, field) or "" for field, _label in tracked}
    status = _rescore_contact(db, contact_id, role)
    changes: list[ContactFieldChange] = []
    if status == "rescored":
        after = _contact_for_bulk_action(db, contact_id, role)
        for field, label in tracked:
            new_value = getattr(after, field) or ""
            if snapshot[field] != new_value:
                changes.append(ContactFieldChange(field=field, label=label, old=snapshot[field], new=new_value))
    return ContactRescoreResponse(id=contact_id, status=status, changes=changes)


@app.post("/recruiter-numbers/{contact_id}/rescore", response_model=ContactRescoreResponse)
def rescore_recruiter_number(contact_id: int, db: Session = Depends(get_db)) -> ContactRescoreResponse:
    return _rescore_contact_with_diff(db, contact_id, "recruiter")


@app.post("/employer-numbers/{contact_id}/rescore", response_model=ContactRescoreResponse)
def rescore_employer_number(contact_id: int, db: Session = Depends(get_db)) -> ContactRescoreResponse:
    return _rescore_contact_with_diff(db, contact_id, "employer")


@app.post("/recruiter-numbers/bulk-delete", response_model=BulkContactActionResponse)
def bulk_delete_recruiter_numbers(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _soft_delete_contact(session, contact_id, "recruiter")
    )


@app.post("/employer-numbers/bulk-delete", response_model=BulkContactActionResponse)
def bulk_delete_employer_numbers(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _soft_delete_contact(session, contact_id, "employer")
    )


@app.post("/recruiter-numbers/bulk-rescore", response_model=BulkContactActionResponse)
def bulk_rescore_recruiter_numbers(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _rescore_contact(session, contact_id, "recruiter")
    )


@app.post("/employer-numbers/bulk-rescore", response_model=BulkContactActionResponse)
def bulk_rescore_employer_numbers(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _rescore_contact(session, contact_id, "employer")
    )


@app.post("/recruiter-numbers/bulk-mark-employer", response_model=BulkContactActionResponse)
def bulk_mark_recruiter_numbers_as_employer(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _bulk_add_contact_role(session, contact_id, "employer")
    )


@app.post("/employer-numbers/bulk-mark-recruiter", response_model=BulkContactActionResponse)
def bulk_mark_employer_numbers_as_recruiter(
    payload: BulkContactActionRequest,
    db: Session = Depends(get_db),
) -> BulkContactActionResponse:
    return _bulk_contact_response(
        db, payload.contact_ids, lambda session, contact_id: _bulk_add_contact_role(session, contact_id, "recruiter")
    )


@app.get("/recruiter-opportunities", response_model=RecruiterOpportunityListResponse)
def list_recruiter_opportunities(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    status: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    job_title: str | None = Query(default=None, max_length=255),
    end_client: str | None = Query(default=None, max_length=255),
    location: str | None = Query(default=None, max_length=255),
    employment_type: str | None = Query(default=None, max_length=40),
    work_mode: str | None = Query(default=None, max_length=80),
    job_confidence: str | None = Query(default=None, max_length=20),
    extension_likely: str | None = Query(default=None, max_length=20),
    sort: str = Query("newest"),
    db: Session = Depends(get_db),
) -> RecruiterOpportunityListResponse:
    if sort not in {"newest", "oldest"}: raise HTTPException(status_code=422, detail="Invalid sort. Must be one of: newest, oldest")
    query = db.query(RecruiterOpportunity).outerjoin(PremiumNumberContact, PremiumNumberContact.id == RecruiterOpportunity.recruiter_number_id).filter(RecruiterOpportunity.owner_id == settings.owner_id)
    if status:
        status_values = [value.strip() for value in status.split(",") if value.strip() in OPPORTUNITY_STATUS_VALUES]
        if status_values:
            query = query.filter(RecruiterOpportunity.status.in_(status_values))
    if source_type in {"gmail", "nvoids"}:
        query = query.filter(RecruiterOpportunity.source_type == source_type)
    # Same rule as list_candidates: a text search spans every date, an explicit
    # date_filter still wins. See _text_search_active.
    if _text_search_active(q, job_title, end_client, location, employment_type, work_mode, job_confidence, extension_likely):
        mail_date = None

    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        query = query.filter(RecruiterOpportunity.received_at >= start, RecruiterOpportunity.received_at < end)
    elif mail_date:
        selected = date.fromisoformat(mail_date)
        start, end = _mail_date_utc_window(selected)
        query = query.filter(RecruiterOpportunity.received_at.is_not(None))
        query = query.filter(RecruiterOpportunity.received_at >= start, RecruiterOpportunity.received_at < end)
    if q and q.strip():
        needle=f"%{q.strip()}%"; digits_only=re.sub(r"\D","",q)
        clauses=[RecruiterOpportunity.email_subject.ilike(needle),RecruiterOpportunity.email_sender.ilike(needle),RecruiterOpportunity.job_title.ilike(needle),RecruiterOpportunity.end_client.ilike(needle),RecruiterOpportunity.location.ilike(needle),RecruiterOpportunity.extracted_skills.ilike(needle),PremiumNumberContact.recruiter_name.ilike(needle),PremiumNumberContact.recruiter_email.ilike(needle),PremiumNumberContact.display_phone_number.ilike(needle)]
        if digits_only: clauses.append(PremiumNumberContact.normalized_phone_number.ilike(f"%{digits_only}%"))
        query=query.filter(or_(*clauses))
    for value,column in (
        (job_title,RecruiterOpportunity.job_title),
        (end_client,RecruiterOpportunity.end_client),
        (location,RecruiterOpportunity.location),
        (employment_type,RecruiterOpportunity.employment_type),
        (work_mode,RecruiterOpportunity.work_mode),
        (job_confidence,RecruiterOpportunity.job_confidence),
        (extension_likely,RecruiterOpportunity.extension_likely),
    ):
        if value and value.strip(): query=query.filter(column.ilike(f"%{value.strip()}%"))
    # Mirrors is_hidden_nvoids_placeholder_recruiter - kept in the query (not just the
    # items list below) so `total` and the paginated rows agree; otherwise a page can
    # come back with fewer items than `limit` (or empty) while `total`/`has_next` still
    # count the hidden placeholder rows.
    query = query.filter(not_(or_(
        and_(
            PremiumNumberContact.normalized_phone_number.ilike("nvoids-%"),
            func.lower(PremiumNumberContact.display_phone_number) == "unknown",
            PremiumNumberContact.first_detected_email_id.is_(None),
        ),
        and_(
            RecruiterOpportunity.recruiter_number_id.is_not(None),
            or_(PremiumNumberContact.normalized_phone_number.is_(None), PremiumNumberContact.normalized_phone_number == ""),
        ),
    )))
    total=query.count()
    order=(RecruiterOpportunity.received_at.asc(),RecruiterOpportunity.id.asc()) if sort=="oldest" else (RecruiterOpportunity.received_at.desc(),RecruiterOpportunity.id.desc())
    rows=query.order_by(*order).offset(cursor).limit(limit+1).all(); has_next=len(rows)>limit; rows=rows[:limit]
    recruiter_ids = sorted({row.recruiter_number_id for row in rows})
    recruiter_rows = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id.in_(recruiter_ids),
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .all()
        if recruiter_ids
        else []
    )
    recruiter_map = {row.id: row for row in recruiter_rows}
    items = [
        _recruiter_opportunity_response(row, recruiter, row.record_id)
        for row in rows
        for recruiter in [recruiter_map.get(row.recruiter_number_id)]
        if not is_hidden_nvoids_placeholder_recruiter(recruiter)
    ]
    return RecruiterOpportunityListResponse(items=items, next_cursor=cursor+limit if has_next else None, has_next=has_next, total=total)


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
        result = _run_nvoids_sync(
            db,
            max_items=resolved_batch_limit,
            role_manifest_enabled=bool(user_settings.feature_role_manifest_enabled),
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


@app.post("/recent-runs/skipped/retry", response_model=JobEnqueueResponse, status_code=202)
def retry_recent_run_skipped_items(
    payload: RecentRunSkippedItemRetryRequest,
    db: Session = Depends(get_db),
) -> JobEnqueueResponse:
    if not payload.skipped_item_ids:
        raise HTTPException(status_code=422, detail="skipped_item_ids must not be empty")
    rows = (
        db.query(RecentRunSkippedItem)
        .filter(
            RecentRunSkippedItem.owner_id == settings.owner_id,
            RecentRunSkippedItem.id.in_(payload.skipped_item_ids),
        )
        .all()
    )
    message_ids = list(dict.fromkeys(row.external_message_id for row in rows if row.external_message_id))
    if not message_ids:
        raise HTTPException(status_code=400, detail="Selected items have no Gmail message id to retry")
    return _enqueue_retry_selected_skipped_items(db, message_ids)


@app.get("/records/{record_id}", response_model=RecordDetailResponse)
def get_record_detail(record_id: str, db: Session = Depends(get_db)) -> RecordDetailResponse:
    record = opportunity_lineage_service.get_record(
        db,
        owner_id=settings.owner_id,
        record_id=record_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")

    emails = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.record_id == record.id)
        .order_by(RecruiterEmail.created_at.asc(), RecruiterEmail.id.asc())
        .all()
    )
    external = (
        db.query(ExternalOpportunity)
        .filter(ExternalOpportunity.owner_id == settings.owner_id, ExternalOpportunity.record_id == record.id)
        .order_by(ExternalOpportunity.created_at.asc(), ExternalOpportunity.id.asc())
        .first()
    )
    source_email = emails[0] if emails else None
    if record.origin_type == "nvoids" and external is not None:
        source = {
            "type": "nvoids",
            "external_opportunity_id": external.id,
            "state": external.bridge_status,
            "subject": external.role,
            "sender": external.recruiter_email or external.recruiter_name,
        }
    else:
        source = {
            "type": "gmail",
            "recruiter_email_id": source_email.id if source_email else None,
            "state": source_email.state if source_email else None,
            "subject": source_email.subject if source_email else "",
            "sender": source_email.sender if source_email else "",
        }

    lineage = (
        db.query(OpportunityLineage)
        .filter(
            OpportunityLineage.owner_id == settings.owner_id,
            OpportunityLineage.id == record.internal_lineage_id,
        )
        .first()
        if record.internal_lineage_id
        else None
    )
    event_query = db.query(OpportunityLifecycleEvent).filter(
        OpportunityLifecycleEvent.owner_id == settings.owner_id,
        OpportunityLifecycleEvent.lineage_id == lineage.id,
    ) if lineage else None
    events = (
        event_query.order_by(
            OpportunityLifecycleEvent.occurred_at.desc(),
            OpportunityLifecycleEvent.id.desc(),
        ).limit(50).all()
        if event_query is not None
        else []
    )
    event_count = event_query.count() if event_query is not None else 0

    opportunity = (
        db.query(RecruiterOpportunity)
        .filter(
            RecruiterOpportunity.owner_id == settings.owner_id,
            RecruiterOpportunity.record_id == record.id,
        )
        .order_by(RecruiterOpportunity.created_at.asc(), RecruiterOpportunity.id.asc())
        .first()
    )
    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == opportunity.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
        if opportunity is not None
        else None
    )
    user_settings = _get_settings(db)
    applications_enabled = bool(user_settings.feature_applications_enabled)
    resume_tracking_enabled = bool(user_settings.feature_resume_tracking_enabled)
    appts_rows = (
        opportunity_lineage_service.applications_for_record(
            db,
            owner_id=settings.owner_id,
            record_id=record.id,
            model=AppTSApplication,
        )
        if applications_enabled
        else []
    )
    legacy_rows = (
        [
            row
            for row in opportunity_lineage_service.applications_for_record(
                db,
                owner_id=settings.owner_id,
                record_id=record.id,
                model=Application,
            )
            if row.promoted_to_appts_application_id is None
        ]
        if resume_tracking_enabled
        else []
    )
    return RecordDetailResponse(
        record_id=record.id,
        origin_type=record.origin_type,
        created_at=record.created_at,
        source=source,
        lineage=(
            {
                "lineage_id": lineage.id,
                "current_status": lineage.current_status,
                "closed_at": lineage.closed_at,
                "event_count": event_count,
            }
            if lineage
            else None
        ),
        recruiter_opportunity=(
            _recruiter_opportunity_response(opportunity, recruiter, record.id)
            if opportunity
            else None
        ),
        applications_enabled=applications_enabled,
        resume_tracking_enabled=resume_tracking_enabled,
        applications=[
            _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)
            for row in appts_rows
        ],
        legacy_applications=[
            _application_response(db, row, include_events=True)
            for row in legacy_rows
        ],
        emails=[
            {
                "recruiter_email_id": email.id,
                "recipient_email": email.recipient_email,
                "cc_email": email.cc_email,
                "sent_status": email.sent_status,
                "sent_at": email.sent_at,
            }
            for email in emails
        ],
        outcomes=opportunity_lineage_service.record_outcomes(
            db,
            owner_id=settings.owner_id,
            record_id=record.id,
        ),
        lifecycle_events=[
            {
                "id": event.id,
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "actor": event.actor,
                "process_name": event.process_name,
                "related_record_type": event.related_record_type,
                "related_record_id": event.related_record_id,
                "note": event.note,
                "metadata": _json_object(event.metadata_json),
            }
            for event in events
        ],
    )


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
        old_status = row.status
        row.status = payload.status
        if payload.status != old_status:
            lineage = opportunity_lineage_service.get_lineage_for_opportunity(
                db,
                owner_id=settings.owner_id,
                recruiter_opportunity_id=row.id,
            )
            if lineage is None:
                logger.warning(
                    "Missing opportunity lineage for recruiter opportunity %s during status change",
                    row.id,
                )
            else:
                opportunity_lineage_service.record_event(
                    db,
                    lineage_id=lineage.id,
                    event_type="status_changed",
                    process_name="main_api",
                    related_record_type="RecruiterOpportunity",
                    related_record_id=row.id,
                    metadata={"from": old_status, "to": payload.status},
                )
                if payload.status in {"Closed", "Not Interested"}:
                    lineage.current_status = "closed"
                    lineage.closed_at = utc_now()
                else:
                    lineage.current_status = "active"
                    lineage.closed_at = None
    if payload.notes is not None:
        row.notes = payload.notes
    for field in (
        "job_title",
        "location",
        "work_mode",
        "visa_restrictions",
        "resume_file_name",
        "implementation_partner",
        "prime_vendor",
        "end_client",
        "domain",
        "extracted_skills",
        "employment_type",
        "rate_amount",
        "rate_currency",
        "rate_unit",
        "contract_duration",
        "relocation_required",
        "extension_likely",
        "end_client_confirmed",
        "job_confidence",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(row, field, value)
    for nullable_field in ("rate_amount", "relocation_required"):
        if nullable_field in payload.model_fields_set:
            setattr(row, nullable_field, getattr(payload, nullable_field))
    db.commit()
    db.refresh(row)
    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == row.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    return _recruiter_opportunity_response(row, recruiter, row.record_id)


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

    active_application = (
        db.query(Application.id)
        .filter(
            Application.owner_id == settings.owner_id,
            Application.recruiter_opportunity_id == opportunity_id,
            Application.deleted_at.is_(None),
            Application.status.not_in(APPLICATION_CLOSED_STATUS_VALUES),
        )
        .first()
    )
    if active_application:
        raise HTTPException(
            status_code=409,
            detail="Opportunity is used by an active application; close or delete those applications first",
        )

    recruiter_number_id = row.recruiter_number_id
    lineage = opportunity_lineage_service.detach_recruiter_opportunity_for_deletion(
        db,
        owner_id=settings.owner_id,
        recruiter_opportunity_id=row.id,
        process_name="main_api",
    )
    if lineage is None:
        logger.warning(
            "Missing opportunity lineage for recruiter opportunity %s during deletion",
            row.id,
        )
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
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == settings.owner_id,
                PremiumNumberContact.id == recruiter_number_id,
                PremiumNumberContact.deleted_at.is_(None),
                PremiumNumberContact.deleted_at.is_(None),
            )
            .first()
        )
        if recruiter:
            recruiter.is_recruiter = False
            recruiter.active_recruiter_lead_id = None
            if not recruiter.is_employer:
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
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == row.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
            PremiumNumberContact.deleted_at.is_(None),
        )
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
    return _recruiter_opportunity_response(row, recruiter, row.record_id)


@app.post("/recruiter-opportunities/{opportunity_id}/refresh-ai-metadata", response_model=RecruiterOpportunityResponse)
def refresh_recruiter_opportunity_ai_metadata(
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

    if row.source_type == "nvoids":
        if not row.external_opportunity_id:
            raise HTTPException(status_code=422, detail="Nvoids opportunity is missing its source posting")
        item = (
            db.query(ExternalOpportunity)
            .filter(
                ExternalOpportunity.owner_id == settings.owner_id,
                ExternalOpportunity.id == row.external_opportunity_id,
            )
            .first()
        )
        if not item:
            raise HTTPException(status_code=404, detail="Source Nvoids posting not found")
        parsed, parser_details = external_feed_service.compute_nvoids_ai_parse(db, owner_id=settings.owner_id, item=item)
        ai_extraction = job_metadata_ai_extraction_from_parsed(parsed, parser_details)
        external_feed_service.phone_intelligence_workflow.refresh_nvoids_opportunity_metadata(
            db,
            row,
            item,
            item.raw_body or "",
            ai_extraction,
        )
    elif row.source_type == "gmail":
        if not row.source_email_id:
            raise HTTPException(status_code=422, detail="Gmail opportunity is missing its source email")
        email = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == row.source_email_id)
            .first()
        )
        if not email:
            raise HTTPException(status_code=404, detail="Source Gmail email not found")
        user_settings = _get_settings(db)
        parsed, parser_details = parse_email_with_details(
            email.subject,
            email.body,
            source="gmail",
            ai_extractor_enabled=user_settings.feature_ai_extractor_enabled,
        )
        ai_extraction = job_metadata_ai_extraction_from_parsed(parsed, parser_details)
        external_feed_service.phone_intelligence_workflow.refresh_gmail_opportunity_metadata(
            db,
            row,
            email,
            ai_extraction,
        )
    else:
        raise HTTPException(
            status_code=422, detail=f"AI metadata refresh is not supported for source '{row.source_type}'"
        )
    db.commit()
    db.refresh(row)
    recruiter = (
        db.query(PremiumNumberContact)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == row.recruiter_number_id,
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    return _recruiter_opportunity_response(row, recruiter, row.record_id)


@app.get("/appts/bookmarked-requirements", response_model=CandidateListResponse)
def list_appts_bookmarked_requirements(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    source: str | None = Query(default=None),
    sendability: str | None = Query(default=None),
    has_resume: bool | None = Query(default=None),
    min_ats_score: float | None = Query(default=None, ge=0, le=100),
    max_ats_score: float | None = Query(default=None, ge=0, le=100),
    role: str | None = Query(default=None, max_length=200),
    interview_type: str | None = Query(default=None, max_length=255),
    location: str | None = Query(default=None, max_length=200),
    sender: str | None = Query(default=None, max_length=255),
    recipient: str | None = Query(default=None, max_length=255),
    ats_strength: str | None = Query(default=None),
    contact_status: str | None = Query(default=None),
    verification: str | None = Query(default=None),
    following: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> CandidateListResponse:
    _require_applications_enabled(db)
    valid_sorts = {"newest", "oldest", "highest_score", "lowest_score"}
    if sort not in valid_sorts:
        raise HTTPException(status_code=422, detail=f"Invalid sort. Must be one of: {', '.join(sorted(valid_sorts))}")
    if min_ats_score is not None and max_ats_score is not None and min_ats_score > max_ats_score:
        raise HTTPException(status_code=422, detail="min_ats_score must be <= max_ats_score")
    query = db.query(RecruiterEmail).filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.state == "needs_review", RecruiterEmail.marked_for_tracking.is_(True))
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        query = query.filter(RecruiterEmail.created_at >= start, RecruiterEmail.created_at < end)
    # ponytail: filter/sort fields mirror list_candidates (main.py ~4645), scoped to the
    # Bookmarked Requirements tab's smaller field set. Duplicated rather than extracted
    # since list_candidates carries state/mail_date/company-join logic this endpoint
    # doesn't need; pull into a shared helper if a third caller needs this subset.
    if source:
        values = [value.strip() for value in source.split(",") if value.strip()]
        if values:
            query = query.filter(RecruiterEmail.source.in_(values))
    if sendability:
        statuses = set().union(*(SENDABILITY_BUCKETS.get(value.strip(), frozenset()) for value in sendability.split(",")))
        if statuses:
            query = query.filter(RecruiterEmail.sendability_status.in_(statuses))
    if has_resume is not None:
        clause = RecruiterEmail.resume_file_name.is_not(None) & (RecruiterEmail.resume_file_name != "")
        query = query.filter(clause if has_resume else ~clause)
    if min_ats_score is not None:
        query = query.filter(RecruiterEmail.ats_score >= min_ats_score)
    if max_ats_score is not None:
        query = query.filter(RecruiterEmail.ats_score <= max_ats_score)
    strength_clause = _ats_strength_clause(ats_strength)
    if strength_clause is not None:
        query = query.filter(strength_clause)
    for value, column in ((role, RecruiterEmail.role), (interview_type, RecruiterEmail.interview_type), (location, RecruiterEmail.location), (sender, RecruiterEmail.sender), (recipient, RecruiterEmail.recipient_email)):
        if value and value.strip():
            query = query.filter(column.ilike(f"%{value.strip()}%"))

    total = query.count()
    if sort == "highest_score":
        query = query.order_by(RecruiterEmail.ats_score.desc(), RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())
    elif sort == "lowest_score":
        query = query.order_by(RecruiterEmail.ats_score.asc(), RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())
    elif sort == "oldest":
        query = query.order_by(RecruiterEmail.created_at.asc(), RecruiterEmail.id.asc())
    else:
        query = query.order_by(RecruiterEmail.created_at.desc(), RecruiterEmail.id.desc())
    badge_selection = _badge_filter_selection(contact_status=contact_status, verification=verification, following=following)
    if badge_selection:
        matching = _filter_rows_by_badges(db, settings.owner_id, query.all(), badge_selection)
        total = len(matching)
        visible, next_cursor, has_next = _paginate_items(matching, cursor=cursor, limit=limit)
    else:
        rows = query.offset(cursor).limit(limit + 1).all()
        visible = rows[:limit]
        has_next = len(rows) > limit
        next_cursor = cursor + limit if has_next else None
    items = [_serialize_candidate_for_review(db, row) for row in visible]
    return CandidateListResponse(items=items, next_cursor=next_cursor, has_next=has_next, total=total)


@app.post("/appts/applications/manual", response_model=ApplicationResponse, status_code=201)
def create_appts_manual(payload: ManualApplicationCreateRequest, response: Response, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    try:
        row, created = appts_service.create_tracked_application_manual(db, owner_id=settings.owner_id, **payload.model_dump())
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit(); db.refresh(row)
    if created: appts_service.enqueue_embedding_generation(row.id)
    response.status_code = 201 if created else 200
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.post("/appts/applications", response_model=ApplicationResponse, status_code=201)
def create_appts_from_opportunity(payload: ApplicationCreateRequest, response: Response, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    try:
        row, created = appts_service.create_tracked_application_from_opportunity(db, owner_id=settings.owner_id, **payload.model_dump())
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit(); db.refresh(row)
    if created: appts_service.enqueue_embedding_generation(row.id)
    response.status_code = 201 if created else 200
    return _application_response(db, row, models=appts_service.APPTS_MODELS)


@app.post("/appts/applications/from-submission/{legacy_application_id}", response_model=ApplicationResponse, status_code=201)
def promote_submission_to_appts(legacy_application_id: int, response: Response, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row, created = appts_service.promote_legacy_application(db, _get_application(db, legacy_application_id), owner_id=settings.owner_id)
    db.commit(); db.refresh(row)
    if created: appts_service.enqueue_embedding_generation(row.id)
    response.status_code = 201 if created else 200
    return _application_response(db, row, models=appts_service.APPTS_MODELS)


@app.get("/appts/applications", response_model=ApplicationListResponse)
def list_appts_applications(
    cursor: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100), status: str | None = Query(default=None),
    q: str | None = Query(default=None), sort: str = Query("newest"), date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None), date_to: date | None = Query(default=None),
    company: str | None = Query(default=None, max_length=255), recruiter: str | None = Query(default=None, max_length=255),
    end_client: str | None = Query(default=None, max_length=255), role: str | None = Query(default=None, max_length=255),
    has_premium_contact: bool | None = Query(default=None), tracked: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApplicationListResponse:
    _require_applications_enabled(db)
    if sort not in {"newest", "oldest", "next_action"}: raise HTTPException(status_code=422, detail="Invalid sort")
    query = db.query(AppTSApplication).filter(AppTSApplication.owner_id == settings.owner_id, AppTSApplication.deleted_at.is_(None))
    if status:
        if status not in APPLICATION_STATUS_VALUES: raise HTTPException(status_code=422, detail="Invalid application status")
        query = query.filter(AppTSApplication.status == status)
    if q and q.strip():
        like = f"%{q.strip()}%"; query = query.filter(or_(AppTSApplication.recruiter_name_snapshot.ilike(like), AppTSApplication.recruiter_company_snapshot.ilike(like), AppTSApplication.job_title_snapshot.ilike(like), AppTSApplication.end_client_snapshot.ilike(like), AppTSApplication.manual_recruiter_email.ilike(like)))
    if company and company.strip():
        like = f"%{company.strip()}%"; query = query.filter(or_(AppTSApplication.recruiter_company_snapshot.ilike(like), AppTSApplication.end_client_snapshot.ilike(like)))
    if recruiter and recruiter.strip():
        like = f"%{recruiter.strip()}%"; query = query.filter(or_(AppTSApplication.recruiter_name_snapshot.ilike(like), AppTSApplication.manual_recruiter_email.ilike(like)))
    if end_client and end_client.strip(): query = query.filter(AppTSApplication.end_client_snapshot.ilike(f"%{end_client.strip()}%"))
    if role and role.strip(): query = query.filter(AppTSApplication.job_title_snapshot.ilike(f"%{role.strip()}%"))
    if has_premium_contact is not None: query = query.filter(AppTSApplication.recruiter_contact_id.is_not(None) if has_premium_contact else AppTSApplication.recruiter_contact_id.is_(None))
    if tracked is not None: query = query.filter(AppTSApplication.recruiter_opportunity_id.is_not(None) if tracked else AppTSApplication.recruiter_opportunity_id.is_(None))
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to); query = query.filter(AppTSApplication.created_at >= start, AppTSApplication.created_at < end)
    total = query.count()
    query = query.order_by(AppTSApplication.created_at.asc(), AppTSApplication.id.asc()) if sort == "oldest" else query.order_by(AppTSApplication.next_action_at.is_(None), AppTSApplication.next_action_at.asc(), AppTSApplication.id.asc()) if sort == "next_action" else query.order_by(AppTSApplication.created_at.desc(), AppTSApplication.id.desc())
    rows = query.offset(cursor).limit(limit + 1).all(); visible = rows[:limit]
    return ApplicationListResponse(items=[_application_response(db, row, models=appts_service.APPTS_MODELS) for row in visible], next_cursor=cursor + limit if len(rows) > limit else None, has_next=len(rows) > limit, total=total)


@app.get("/appts/applications/{application_id}", response_model=ApplicationResponse)
def get_appts_application(application_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    return _application_response(db, _get_appts_application(db, application_id), include_events=True, models=appts_service.APPTS_MODELS)


@app.get("/appts/applications/{application_id}/sent-details", response_model=SentItemDetailsResponse)
def get_appts_application_sent_details(application_id: int, db: Session = Depends(get_db)) -> SentItemDetailsResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    if not row.source_recruiter_email_id:
        raise HTTPException(status_code=404, detail="This application has no linked sourcing details")
    email = _get_candidate_for_review(db, row.source_recruiter_email_id)
    return _build_sent_item_details(db, email)


@app.patch("/appts/applications/{application_id}", response_model=ApplicationResponse)
def patch_appts_application(application_id: int, payload: ApplicationPatchRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    if payload.status: application_service.update_status(db, row, new_status=payload.status, closed_reason_code=payload.closed_reason_code, models=appts_service.APPTS_MODELS)
    if payload.next_action_type is not None or payload.next_action_at is not None: application_service.set_next_action(db, row, next_action_type=payload.next_action_type, next_action_at=payload.next_action_at, models=appts_service.APPTS_MODELS)
    if payload.closed_reason is not None: row.closed_reason = payload.closed_reason
    db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.post("/appts/applications/{application_id}/events", response_model=ApplicationEventResponse, status_code=201)
def create_appts_event(application_id: int, payload: ApplicationEventCreateRequest, db: Session = Depends(get_db)) -> ApplicationEventResponse:
    _require_applications_enabled(db)
    event = application_service.append_event(db, _get_appts_application(db, application_id), event_type=payload.event_type, note=payload.note, linked_recruiter_email_id=payload.linked_recruiter_email_id, models=appts_service.APPTS_MODELS)
    db.commit(); db.refresh(event); return ApplicationEventResponse.model_validate(event)


@app.post("/appts/applications/{application_id}/rtr", response_model=ApplicationResponse, status_code=201)
def create_appts_rtr(application_id: int, payload: ApplicationRTRRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id); application_service.request_rtr(db, row, **payload.model_dump(), models=appts_service.APPTS_MODELS); db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.patch("/appts/applications/{application_id}/rtr/{rtr_id}", response_model=ApplicationResponse)
def update_appts_rtr(application_id: int, rtr_id: int, payload: ApplicationRTRUpdateRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    rtr = db.query(appts_service.APPTS_MODELS.rtr_cls).filter_by(owner_id=settings.owner_id, application_id=row.id, id=rtr_id).first()
    if rtr is None:
        raise HTTPException(status_code=404, detail="Application RTR not found")
    try:
        if payload.status == "confirmed":
            application_service.confirm_rtr(db, row, rtr, proof_attachment_id=payload.proof_attachment_id, proof_recruiter_email_id=payload.proof_recruiter_email_id, models=appts_service.APPTS_MODELS)
        else:
            application_service.expire_or_revoke_rtr(db, row, rtr, new_status=payload.status, models=appts_service.APPTS_MODELS)
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.post("/appts/applications/{application_id}/submit-to-client", response_model=ApplicationResponse)
def submit_appts_to_client(application_id: int, payload: ApplicationSubmitToClientRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    try: application_service.submit_to_client(db, row, override_duplicate_warning=payload.override_duplicate_warning, models=appts_service.APPTS_MODELS)
    except application_service.ApplicationDuplicateWarning as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "duplicates": [{"id": candidate.id, "job_title_snapshot": candidate.job_title_snapshot, "end_client_snapshot": candidate.end_client_snapshot, "status": candidate.status, "created_at": candidate.created_at.isoformat()} for candidate in exc.candidates]}) from exc
    db.commit(); db.refresh(row); return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.post("/appts/applications/{application_id}/interviews", response_model=ApplicationResponse, status_code=201)
def create_appts_interview(application_id: int, payload: ApplicationInterviewCreateRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id); application_service.add_interview(db, row, **payload.model_dump(), models=appts_service.APPTS_MODELS); db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.patch("/appts/applications/{application_id}/interviews/{interview_id}", response_model=ApplicationResponse)
def patch_appts_interview(application_id: int, interview_id: int, payload: ApplicationInterviewPatchRequest, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    interview = db.query(appts_service.APPTS_MODELS.interview_cls).filter_by(owner_id=settings.owner_id, application_id=row.id, id=interview_id).first()
    if interview is None:
        raise HTTPException(status_code=404, detail="Application interview not found")
    application_service.update_interview(db, interview, **payload.model_dump(exclude_unset=True), models=appts_service.APPTS_MODELS)
    db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.delete("/appts/applications/{application_id}/interviews/{interview_id}", response_model=ApplicationResponse)
def delete_appts_interview(application_id: int, interview_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    _require_applications_enabled(db)
    row = _get_appts_application(db, application_id)
    interview = db.query(appts_service.APPTS_MODELS.interview_cls).filter_by(owner_id=settings.owner_id, application_id=row.id, id=interview_id).first()
    if interview is None:
        raise HTTPException(status_code=404, detail="Application interview not found")
    application_service.delete_interview(db, interview, models=appts_service.APPTS_MODELS)
    db.commit(); db.refresh(row)
    return _application_response(db, row, include_events=True, models=appts_service.APPTS_MODELS)


@app.post("/applications", response_model=ApplicationResponse, status_code=201)
def create_application_route(
    payload: ApplicationCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    try:
        row, created = application_service.create_application(
            db,
            owner_id=settings.owner_id,
            resume_asset_id=payload.resume_asset_id,
            recruiter_opportunity_id=payload.recruiter_opportunity_id,
            dedupe_key=payload.dedupe_key,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    response.status_code = 201 if created else 200
    return _application_response(db, row)


@app.post("/applications/manual", response_model=ApplicationResponse, status_code=201)
def create_manual_application_route(
    payload: ManualApplicationCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    _require_resume_tracking_enabled(db)
    try:
        row, created = resume_tracking_service.create_manual_application(
            db,
            owner_id=settings.owner_id,
            **payload.model_dump(exclude={"location_snapshot"}),
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    response.status_code = 201 if created else 200
    return _application_response(db, row, include_events=True)


@app.get("/applications", response_model=ApplicationListResponse)
def list_applications(
    cursor: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
    status: str | None = Query(default=None),
    resume_submission_status: str | None = Query(default=None),
    resume_asset_id: int | None = Query(default=None, gt=0),
    recruiter_contact_id: int | None = Query(default=None, gt=0),
    q: str | None = Query(default=None),
    company: str | None = Query(default=None, max_length=255),
    recruiter: str | None = Query(default=None, max_length=255),
    opportunity_domain: str | None = Query(default=None, max_length=255),
    implementation_partner: str | None = Query(default=None, max_length=255),
    end_client: str | None = Query(default=None, max_length=255),
    role: str | None = Query(default=None, max_length=255),
    has_premium_contact: bool | None = Query(default=None),
    tracked: bool | None = Query(default=None),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> ApplicationListResponse:
    if sort not in {"newest", "oldest", "next_action"}: raise HTTPException(status_code=422, detail="Invalid sort. Must be one of: newest, oldest, next_action")
    query = db.query(Application).outerjoin(RecruiterOpportunity, RecruiterOpportunity.id == Application.recruiter_opportunity_id).outerjoin(PremiumNumberContact, PremiumNumberContact.id == Application.recruiter_contact_id).filter(
        Application.owner_id == settings.owner_id,
        Application.deleted_at.is_(None),
    )
    if status:
        if status not in APPLICATION_STATUS_VALUES:
            raise HTTPException(status_code=422, detail="Invalid application status")
        query = query.filter(Application.status == status)
    if resume_submission_status:
        if resume_submission_status not in RESUME_SUBMISSION_STATUS_VALUES:
            raise HTTPException(status_code=422, detail="Invalid resume submission status")
        query = query.filter(Application.resume_submission_status == resume_submission_status)
    if resume_asset_id is not None:
        query = query.filter(Application.resume_asset_id == resume_asset_id)
    if recruiter_contact_id is not None:
        query = query.filter(Application.recruiter_contact_id == recruiter_contact_id)
    if q and q.strip():
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Application.resume_file_name_snapshot.ilike(needle),
                Application.recruiter_name_snapshot.ilike(needle),
                Application.recruiter_company_snapshot.ilike(needle),
                Application.job_title_snapshot.ilike(needle),
                Application.end_client_snapshot.ilike(needle),
                Application.manual_recruiter_email.ilike(needle),
            )
        )
    if company and company.strip():
        like=f"%{company.strip()}%"; query=query.filter(or_(Application.recruiter_company_snapshot.ilike(like),Application.end_client_snapshot.ilike(like)))
    if recruiter and recruiter.strip():
        like=f"%{recruiter.strip()}%"; query=query.filter(or_(Application.recruiter_name_snapshot.ilike(like),Application.manual_recruiter_email.ilike(like),PremiumNumberContact.recruiter_email.ilike(like)))
    if opportunity_domain and opportunity_domain.strip(): query=query.filter(RecruiterOpportunity.domain.ilike(f"%{opportunity_domain.strip()}%"))
    if implementation_partner and implementation_partner.strip(): query=query.filter(RecruiterOpportunity.implementation_partner.ilike(f"%{implementation_partner.strip()}%"))
    if end_client and end_client.strip(): query=query.filter(RecruiterOpportunity.end_client.ilike(f"%{end_client.strip()}%"))
    if role and role.strip(): query=query.filter(Application.resume_primary_role_snapshot.ilike(f"%{role.strip()}%"))
    if has_premium_contact is not None: query=query.filter(Application.recruiter_contact_id.is_not(None) if has_premium_contact else Application.recruiter_contact_id.is_(None))
    if tracked is not None: query=query.filter(Application.recruiter_opportunity_id.is_not(None) if tracked else Application.recruiter_opportunity_id.is_(None))
    if date_filter:
        start, end = _date_range_utc_window(date_filter, date_from, date_to)
        query = query.filter(Application.resume_submitted_at >= start, Application.resume_submitted_at < end)
    total=query.count()
    if sort=="oldest": query=query.order_by(Application.created_at.asc(),Application.id.asc())
    elif sort=="next_action": query=query.order_by(Application.next_action_at.is_(None),Application.next_action_at.asc(),Application.id.asc())
    else: query=query.order_by(Application.created_at.desc(),Application.id.desc())
    rows=query.offset(cursor).limit(limit+1).all(); visible=rows[:limit]; has_next=len(rows)>limit; next_cursor=cursor+limit if has_next else None
    # ponytail: one current-source lookup pair per visible row; batch only if this tab outgrows 100 rows/page.
    items = [_application_response(db, row) for row in visible]
    return ApplicationListResponse(items=items, next_cursor=next_cursor, has_next=has_next, total=total)


@app.get("/applications/dashboard-summary", response_model=ApplicationDashboardSummaryResponse)
def applications_dashboard_summary(db: Session = Depends(get_db)) -> ApplicationDashboardSummaryResponse:
    pending_suggestions = int(
        db.query(func.count(ApplicationSuggestion.id))
        .filter(
            ApplicationSuggestion.owner_id == settings.owner_id,
            ApplicationSuggestion.status == "pending",
        )
        .scalar()
        or 0
    )
    return ApplicationDashboardSummaryResponse(
        **application_service.dashboard_summary(db, settings.owner_id),
        pending_suggestions=pending_suggestions,
    )


@app.get("/applications/match", response_model=OpportunityMatchListResponse)
def match_opportunities_for_resume(
    resume_asset_id: int = Query(gt=0),
    limit: int = Query(default=25, ge=1, le=100),
    exclude_already_applied: bool = True,
    db: Session = Depends(get_db),
) -> OpportunityMatchListResponse:
    try:
        matches = application_intelligence_service.rank_opportunities_for_resume(
            db,
            owner_id=settings.owner_id,
            resume_asset_id=resume_asset_id,
            limit=limit,
            exclude_already_applied=exclude_already_applied,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    opportunity_ids = [match.opportunity_id for match in matches]
    opportunities = {
        row.id: row
        for row in (
            db.query(RecruiterOpportunity)
            .filter(
                RecruiterOpportunity.owner_id == settings.owner_id,
                RecruiterOpportunity.id.in_(opportunity_ids),
            )
            .all()
            if opportunity_ids
            else []
        )
    }
    recruiter_ids = {row.recruiter_number_id for row in opportunities.values()}
    recruiters = {
        row.id: row
        for row in (
            db.query(PremiumNumberContact)
            .filter(
                PremiumNumberContact.owner_id == settings.owner_id,
                PremiumNumberContact.id.in_(recruiter_ids),
                PremiumNumberContact.deleted_at.is_(None),
            )
            .all()
            if recruiter_ids
            else []
        )
    }
    return OpportunityMatchListResponse(
        items=[
            OpportunityMatchResponse(
                opportunity=_recruiter_opportunity_response(
                    opportunities[match.opportunity_id],
                    recruiters.get(opportunities[match.opportunity_id].recruiter_number_id),
                    opportunities[match.opportunity_id].record_id,
                ),
                score=match.score,
                reasons=match.reasons,
            )
            for match in matches
            if match.opportunity_id in opportunities
        ]
    )


@app.get("/recruiter-numbers/{recruiter_number_id}/reputation", response_model=RecruiterReputationResponse)
def get_recruiter_reputation(
    recruiter_number_id: int,
    db: Session = Depends(get_db),
) -> RecruiterReputationResponse:
    recruiter = (
        db.query(PremiumNumberContact.id)
        .filter(
            PremiumNumberContact.owner_id == settings.owner_id,
            PremiumNumberContact.id == recruiter_number_id,
            PremiumNumberContact.is_recruiter.is_(True),
            PremiumNumberContact.deleted_at.is_(None),
        )
        .first()
    )
    if recruiter is None:
        raise HTTPException(status_code=404, detail="Recruiter number not found")
    return RecruiterReputationResponse.model_validate(
        application_intelligence_service.compute_recruiter_reputation(
            db,
            owner_id=settings.owner_id,
            recruiter_contact_id=recruiter_number_id,
        )
    )


@app.get("/applications/suggestions", response_model=ApplicationSuggestionListResponse)
def list_application_suggestions(
    status: str = "pending",
    db: Session = Depends(get_db),
) -> ApplicationSuggestionListResponse:
    if status not in APPLICATION_SUGGESTION_STATUS_VALUES:
        raise HTTPException(status_code=422, detail="Invalid application suggestion status")
    rows = (
        db.query(ApplicationSuggestion)
        .filter(
            ApplicationSuggestion.owner_id == settings.owner_id,
            ApplicationSuggestion.status == status,
        )
        .order_by(ApplicationSuggestion.created_at.desc(), ApplicationSuggestion.id.desc())
        .all()
    )
    return ApplicationSuggestionListResponse(
        items=[_application_suggestion_response(row) for row in rows]
    )


@app.post("/applications/suggestions/{suggestion_id}/accept", response_model=ApplicationResponse)
def accept_application_suggestion(
    suggestion_id: int,
    payload: ApplicationSuggestionResolveRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    suggestion = _get_application_suggestion(db, suggestion_id)
    application = _get_application(db, suggestion.application_id)
    try:
        application_service.accept_suggestion(
            db,
            suggestion,
            application=application,
            override_next_action_at=payload.override_next_action_at,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(application)
    return _application_response(db, application, include_events=True)


@app.post("/applications/suggestions/{suggestion_id}/dismiss", response_model=ApplicationSuggestionResponse)
def dismiss_application_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
) -> ApplicationSuggestionResponse:
    suggestion = _get_application_suggestion(db, suggestion_id)
    application_service.dismiss_suggestion(db, suggestion)
    db.commit()
    db.refresh(suggestion)
    return _application_suggestion_response(suggestion)


@app.post("/applications/reminders/run", response_model=ApplicationSuggestionListResponse)
def run_reminder_sweep_now(db: Session = Depends(get_db)) -> ApplicationSuggestionListResponse:
    created = application_intelligence_service.generate_reminder_sweep_suggestions(
        db,
        owner_id=settings.owner_id,
    )
    db.commit()
    return ApplicationSuggestionListResponse(
        items=[_application_suggestion_response(row) for row in created]
    )


@app.post("/applications/resume-tracking/suggestions/run", response_model=ApplicationSuggestionListResponse)
def run_resume_tracking_sweep_now(db: Session = Depends(get_db)) -> ApplicationSuggestionListResponse:
    _require_resume_tracking_enabled(db)
    created = resume_tracking_service.generate_resume_tracking_suggestions(
        db,
        owner_id=settings.owner_id,
    )
    db.commit()
    return ApplicationSuggestionListResponse(
        items=[_application_suggestion_response(row) for row in created]
    )


@app.get("/resumes/performance-summary", response_model=ResumePerformanceSummaryResponse)
def resume_performance_summary_route(sort: str = Query("recent"), db: Session = Depends(get_db)) -> ResumePerformanceSummaryResponse:
    _require_resume_tracking_enabled(db)
    if sort not in {"recent", "acceptance_desc", "acceptance_asc", "submissions_desc"}: raise HTTPException(status_code=422, detail="Invalid sort")
    items = resume_tracking_service.resume_performance_summary(
        db,
        owner_id=settings.owner_id,
        sort=sort,
        combined=True,
    )
    return ResumePerformanceSummaryResponse(
        items=[
            ResumePerformanceSummaryItem(
                resume=_resume_response(item["resume"]),
                submission_count=int(item["submission_count"]),
                acceptance_rate=float(item["acceptance_rate"]),
            )
            for item in items
        ]
    )


@app.get("/resumes/role-gaps", response_model=RoleGapReportResponse)
def resume_role_gaps_route(
    window_days: int = Query(90, ge=1, le=730),
    min_jds: int = Query(5, ge=1, le=500),
    db: Session = Depends(get_db),
) -> RoleGapReportResponse:
    """Which roles the resume library keeps failing, and what it would take to win them."""
    _require_resume_tracking_enabled(db)
    report = role_gap_service.role_gap_report(
        db,
        owner_id=settings.owner_id,
        window_days=window_days,
        min_jds=min_jds,
    )
    return RoleGapReportResponse.model_validate(report)


@app.get("/resumes/variant-lookup", response_model=VariantLookupResponse)
def resume_variant_lookup_route(
    token: str = Query(..., min_length=1, max_length=500),
    db: Session = Depends(get_db),
) -> VariantLookupResponse:
    """Resolve a variant marker pasted out of a sent email back to the send it came from."""
    _require_resume_tracking_enabled(db)
    parsed = parse_variant_token(token)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail="That does not look like a variant marker. Expected something like CJ-R14-8842.",
        )
    variant_code, email_id = parsed
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if email is None:
        raise HTTPException(status_code=404, detail="No sent email matches that marker.")
    # The email id alone identifies the send; the code is checked so a mistyped or
    # doctored marker surfaces as a mismatch instead of silently resolving elsewhere.
    actual_code = resume_variant_code(email.resume_asset_id)
    if actual_code and variant_code != actual_code:
        raise HTTPException(
            status_code=409,
            detail=f"Marker mismatch: that send used {actual_code}, not {variant_code}.",
        )
    resume = (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == email.resume_asset_id)
        .first()
        if email.resume_asset_id is not None
        else None
    )
    return VariantLookupResponse(
        email_id=email.id,
        variant_code=actual_code or variant_code,
        variant_label=_clean_optional_text(resume.variant_label if resume else None),
        resume_file_name=_clean_optional_text(email.resume_file_name),
        role=_clean_optional_text(email.role),
        subject=_clean_optional_text(email.subject),
        recruiter_email=_clean_optional_text(email.recipient_email),
        sent_at=email.sent_at,
    )


@app.get("/resumes/{resume_asset_id}/funnel", response_model=ResumeFunnelMetricsResponse)
def resume_funnel_route(
    resume_asset_id: int,
    db: Session = Depends(get_db),
) -> ResumeFunnelMetricsResponse:
    _require_resume_tracking_enabled(db)
    resume = (
        db.query(ResumeAsset.id)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.id == resume_asset_id)
        .first()
    )
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found")
    return ResumeFunnelMetricsResponse(
        **resume_tracking_service.combined_resume_funnel_metrics(
            db,
            owner_id=settings.owner_id,
            resume_asset_id=resume_asset_id,
        )
    )


@app.patch(
    "/applications/{application_id}/resume-submission-status",
    response_model=ApplicationResponse,
)
def update_resume_submission_status_route(
    application_id: int,
    payload: ResumeSubmissionStatusUpdateRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    _require_resume_tracking_enabled(db)
    application = _get_application(db, application_id)
    try:
        resume_tracking_service.update_resume_submission_status(
            db,
            application,
            new_status=payload.new_status,
            rejection_detail_tags=payload.rejection_detail_tags,
            note=payload.note,
            force=payload.force,
        )
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(application)
    return _application_response(db, application, include_events=True)


@app.get(
    "/applications/{application_id}/why-this-resume",
    response_model=WhyThisResumeResponse,
)
def get_why_this_resume(
    application_id: int,
    db: Session = Depends(get_db),
) -> WhyThisResumeResponse:
    """Why the resume that went out was chosen, read off the email that sent it.

    The application's own skill-gap snapshot cannot answer this: no application
    carries a linked job description, so that comparison always comes back empty.
    """
    _require_resume_tracking_enabled(db)
    payload = why_this_resume_service.why_this_resume(db, _get_application(db, application_id))
    return WhyThisResumeResponse.model_validate(payload)


@app.get(
    "/applications/{application_id}/skill-gap",
    response_model=ApplicationSkillGapResponse,
)
def get_application_skill_gap(
    application_id: int,
    db: Session = Depends(get_db),
) -> ApplicationSkillGapResponse:
    _require_resume_tracking_enabled(db)
    snapshot = resume_tracking_service.compute_skill_gap(db, _get_application(db, application_id))
    db.commit()
    db.refresh(snapshot)
    return _skill_gap_response(snapshot)


@app.post(
    "/applications/{application_id}/skill-gap/recompute",
    response_model=ApplicationSkillGapResponse,
)
def recompute_application_skill_gap(
    application_id: int,
    db: Session = Depends(get_db),
) -> ApplicationSkillGapResponse:
    _require_resume_tracking_enabled(db)
    snapshot = resume_tracking_service.compute_skill_gap(
        db,
        _get_application(db, application_id),
        force_recompute=True,
    )
    db.commit()
    db.refresh(snapshot)
    return _skill_gap_response(snapshot)


@app.get(
    "/applications/{application_id}/outreach-messages/{message_id}",
    response_model=ApplicationOutreachMessageResponse,
)
def get_application_outreach_message(
    application_id: int,
    message_id: int,
    db: Session = Depends(get_db),
) -> ApplicationOutreachMessageResponse:
    _require_resume_tracking_enabled(db)
    _get_application(db, application_id)
    row = (
        db.query(ApplicationOutreachMessage)
        .filter(
            ApplicationOutreachMessage.owner_id == settings.owner_id,
            ApplicationOutreachMessage.application_id == application_id,
            ApplicationOutreachMessage.id == message_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Outreach message not found")
    return ApplicationOutreachMessageResponse.model_validate(row)


@app.post("/applications/{application_id}/draft-message", response_model=ApplicationDraftMessageResponse)
def draft_application_message(
    application_id: int,
    payload: ApplicationDraftMessageRequest,
    db: Session = Depends(get_db),
) -> ApplicationDraftMessageResponse:
    application = _get_application(db, application_id)
    user_settings = _get_settings(db)
    try:
        draft = application_outreach_service.build_application_draft(
            db,
            application,
            message_kind=payload.message_kind,
            user_settings=user_settings,
            model_name=settings.deepseek_model_fast,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApplicationDraftMessageResponse(**draft.__dict__)


@app.post("/applications/{application_id}/send-message", response_model=ApplicationSendMessageResponse)
def send_application_message(
    application_id: int,
    payload: ApplicationSendMessageRequest,
    db: Session = Depends(get_db),
) -> ApplicationSendMessageResponse:
    application = _get_application(db, application_id)
    try:
        application, gmail_message_id = application_outreach_service.send_application_message(
            db,
            application,
            to=payload.to,
            cc=payload.cc,
            subject=payload.subject,
            body=payload.body,
            thread_id=payload.thread_id,
            message_kind=payload.message_kind,
            include_resume=payload.include_resume,
            attachment_asset_ids=payload.attachment_asset_ids,
            draft_source=payload.draft_source,
            ai_model=payload.ai_model,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Failed to send message: {exc}") from exc
    db.commit()
    db.refresh(application)
    return ApplicationSendMessageResponse(
        sent=True,
        gmail_message_id=gmail_message_id,
        application=_application_response(db, application, include_events=True),
    )


@app.get("/applications/{application_id}", response_model=ApplicationResponse)
def get_application(application_id: int, db: Session = Depends(get_db)) -> ApplicationResponse:
    return _application_response(db, _get_application(db, application_id), include_events=True)


@app.patch("/applications/{application_id}", response_model=ApplicationResponse)
def patch_application(
    application_id: int,
    payload: ApplicationPatchRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    provided = payload.model_fields_set
    if "status" in provided and payload.status is not None:
        if payload.status == "submitted_to_client":
            raise HTTPException(status_code=422, detail="Use the submit-to-client endpoint for this status")
        try:
            application_service.update_status(
                db,
                row,
                new_status=payload.status,
                closed_reason_code=(
                    payload.closed_reason_code if "closed_reason_code" in provided else None
                ),
            )
        except application_service.ApplicationValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif "closed_reason_code" in provided:
        if row.status not in APPLICATION_CLOSED_STATUS_VALUES:
            raise HTTPException(status_code=422, detail="Closed reason code requires a closed application status")
        if (
            payload.closed_reason_code is not None
            and payload.closed_reason_code not in application_service.CLOSED_REASON_CODE_VALUES
        ):
            raise HTTPException(status_code=422, detail="Invalid closed reason code")
        row.closed_reason_code = payload.closed_reason_code
    if "next_action_type" in provided or "next_action_at" in provided:
        application_service.set_next_action(
            db,
            row,
            next_action_type=(
                payload.next_action_type if "next_action_type" in provided else row.next_action_type
            ),
            next_action_at=payload.next_action_at if "next_action_at" in provided else row.next_action_at,
        )
    if "closed_reason" in provided:
        row.closed_reason = payload.closed_reason
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.delete("/applications/{application_id}")
def soft_delete_application(application_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    row = _get_application(db, application_id)
    row.deleted_at = datetime.now(UTC)
    db.commit()
    return {"id": application_id, "deleted": True}


@app.post("/applications/{application_id}/events", response_model=ApplicationEventResponse, status_code=201)
def create_application_event(
    application_id: int,
    payload: ApplicationEventCreateRequest,
    db: Session = Depends(get_db),
) -> ApplicationEventResponse:
    row = _get_application(db, application_id)
    try:
        event = application_service.append_event(
            db,
            row,
            event_type=payload.event_type,
            note=payload.note,
            linked_recruiter_email_id=payload.linked_recruiter_email_id,
        )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(event)
    return ApplicationEventResponse.model_validate(event)


@app.post("/applications/{application_id}/rtr", response_model=ApplicationResponse, status_code=201)
def request_application_rtr(
    application_id: int,
    payload: ApplicationRTRRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    application_service.request_rtr(
        db,
        row,
        role_scope=payload.role_scope,
        end_client_scope=payload.end_client_scope,
        expires_at=payload.expires_at,
    )
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.patch("/applications/{application_id}/rtr/{rtr_id}", response_model=ApplicationResponse)
def update_application_rtr(
    application_id: int,
    rtr_id: int,
    payload: ApplicationRTRUpdateRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    rtr = _get_application_rtr(db, row, rtr_id)
    try:
        if payload.status == "confirmed":
            application_service.confirm_rtr(
                db,
                row,
                rtr,
                proof_attachment_id=payload.proof_attachment_id,
                proof_recruiter_email_id=payload.proof_recruiter_email_id,
            )
        else:
            application_service.expire_or_revoke_rtr(
                db,
                row,
                rtr,
                new_status=payload.status,
            )
    except application_service.ApplicationReferenceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.post("/applications/{application_id}/submit-to-client", response_model=ApplicationResponse)
def submit_application_to_client(
    application_id: int,
    payload: ApplicationSubmitToClientRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    try:
        application_service.submit_to_client(
            db,
            row,
            override_duplicate_warning=payload.override_duplicate_warning,
        )
    except application_service.ApplicationDuplicateWarning as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "duplicates": [
                    {
                        "id": candidate.id,
                        "job_title_snapshot": candidate.job_title_snapshot,
                        "end_client_snapshot": candidate.end_client_snapshot,
                        "status": candidate.status,
                        "created_at": candidate.created_at.isoformat(),
                    }
                    for candidate in exc.candidates
                ],
            },
        ) from exc
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.post("/applications/{application_id}/interviews", response_model=ApplicationResponse, status_code=201)
def create_application_interview(
    application_id: int,
    payload: ApplicationInterviewCreateRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    try:
        application_service.add_interview(
            db,
            row,
            round_type=payload.round_type,
            scheduled_at=payload.scheduled_at,
            format=payload.format,
            interviewer_names=payload.interviewer_names,
            sync_application_status=payload.sync_application_status,
        )
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.patch("/applications/{application_id}/interviews/{interview_id}", response_model=ApplicationResponse)
def patch_application_interview(
    application_id: int,
    interview_id: int,
    payload: ApplicationInterviewPatchRequest,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    interview = _get_application_interview(db, row, interview_id)
    try:
        application_service.update_interview(
            db,
            interview,
            **payload.model_dump(exclude_unset=True),
        )
    except application_service.ApplicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.delete("/applications/{application_id}/interviews/{interview_id}", response_model=ApplicationResponse)
def delete_application_interview(
    application_id: int,
    interview_id: int,
    db: Session = Depends(get_db),
) -> ApplicationResponse:
    row = _get_application(db, application_id)
    interview = _get_application_interview(db, row, interview_id)
    application_service.delete_interview(db, interview)
    db.commit()
    db.refresh(row)
    return _application_response(db, row, include_events=True)


@app.get("/candidates/{email_id}", response_model=EmailResponse)
def get_candidate(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    return _get_candidate_review(email_id, db)


@app.get("/candidates/{email_id}/sent-details", response_model=SentItemDetailsResponse)
def get_sent_item_details(email_id: int, db: Session = Depends(get_db)) -> SentItemDetailsResponse:
    email = _get_candidate_for_review(db, email_id)
    if email.state not in {"needs_review", "approved_sent"}:
        raise HTTPException(status_code=400, detail="Details are only available for needs_review or approved_sent candidates")
    return _build_sent_item_details(db, email)


@app.get("/inbox/conversations", response_model=list[ConversationSummaryResponse])
def get_inbox_conversations(
    recruiter: str | None = Query(default=None, max_length=255),
    subject: str | None = Query(default=None, max_length=500),
    role: str | None = Query(default=None, max_length=255),
    location: str | None = Query(default=None, max_length=255),
    interview_type: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None),
    unread_only: bool | None = Query(default=None),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ConversationSummaryResponse]:
    start, end = _date_range_utc_window(date_filter, date_from, date_to) if date_filter else (None, None)
    return _get_orchestration_service().list_inbox_conversations(db, recruiter=recruiter, subject=subject, role=role, location=location, interview_type=interview_type, status=status, unread_only=unread_only, sort=sort, date_from=start, date_to=end)


@app.post("/inbox/conversations/refresh", response_model=list[ConversationSummaryResponse])
def refresh_inbox_conversations(
    recruiter: str | None = Query(default=None, max_length=255),
    subject: str | None = Query(default=None, max_length=500),
    role: str | None = Query(default=None, max_length=255),
    location: str | None = Query(default=None, max_length=255),
    interview_type: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None),
    unread_only: bool | None = Query(default=None),
    sort: str = Query("newest"),
    date_filter: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ConversationSummaryResponse]:
    start, end = _date_range_utc_window(date_filter, date_from, date_to) if date_filter else (None, None)
    return _get_orchestration_service().refresh_inbox_replies(db, recruiter=recruiter, subject=subject, role=role, location=location, interview_type=interview_type, status=status, unread_only=unread_only, sort=sort, date_from=start, date_to=end)


@app.get("/inbox/conversations/{conversation_id}", response_model=ConversationDetailResponse)
def get_inbox_conversation(conversation_id: int, db: Session = Depends(get_db)) -> ConversationDetailResponse:
    return _get_orchestration_service().get_inbox_conversation(conversation_id, db)


@app.post("/inbox/conversations/{conversation_id}/reply", response_model=ConversationDetailResponse)
def reply_to_inbox_conversation(
    conversation_id: int,
    payload: ConversationReplyRequest,
    db: Session = Depends(get_db),
) -> ConversationDetailResponse:
    return _get_orchestration_service().reply_to_inbox_conversation(conversation_id, payload.body, db)


@app.post("/inbox/conversations/{conversation_id}/read", response_model=ConversationDetailResponse)
def mark_inbox_conversation_read(
    conversation_id: int,
    db: Session = Depends(get_db),
) -> ConversationDetailResponse:
    return _get_orchestration_service().mark_inbox_conversation_read(conversation_id, db)


@app.post("/candidates/{email_id}/approve-send", response_model=EmailResponse)
def approve_and_send(
    email_id: int,
    payload: ApproveSendRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().approve_send(email_id, payload, db)


@app.post(
    "/candidates/{email_id}/send-chat-reply",
    dependencies=[Depends(require_chat_actions_enabled)],
)
def send_chat_reply(
    email_id: int,
    payload: ChatSendReplyRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    email = _get_candidate_for_review(db, email_id)
    if not (email.recipient_email or "").strip():
        raise HTTPException(status_code=400, detail="Recipient email is missing")
    if not (email.external_thread_id or "").strip():
        raise HTTPException(status_code=400, detail="Gmail thread is missing")
    # Resolved before the send, so an unknown id or a file gone from disk fails
    # with nothing delivered rather than delivering the mail without its files.
    documents = _resolve_candidate_documents(db, payload.document_ids)
    message_id = send_reply_with_attachment(
        thread_id=email.external_thread_id,
        to=email.recipient_email,
        cc=email.cc_email,
        subject=(payload.subject or email.subject or "").strip(),
        body=payload.body.strip(),
        attachments=[
            MailAttachment(
                path=item.file_path,
                # The label is the user's name for the file, not a file name -
                # the recruiter gets what was actually uploaded.
                display_name=item.file_name,
                mime_type=item.mime_type,
            )
            for item in documents
        ]
        or None,
    )
    _record_productivity_event(
        db,
        event_type="chat_reply_sent",
        event_source="chat_assistant",
        entity_id=email.id,
        entity_type="RecruiterEmail",
        metadata={
            "gmail_message_id": message_id,
            "attached_documents": [item.file_name for item in documents],
        },
    )
    return {
        "sent": True,
        "message_id": message_id,
        "email_id": email.id,
        "attached_documents": [item.file_name for item in documents],
    }


@app.post(
    "/support/github-issues",
    dependencies=[Depends(require_chat_actions_enabled)],
)
def create_support_github_issue(payload: GithubIssueCreateRequest) -> dict[str, object]:
    body = f"**User report:** {payload.user_report}\n\n**AI summary:** {payload.ai_summary}"
    if payload.context.strip():
        body += f"\n\n**Helpful context:** {payload.context.strip()}"
    try:
        return create_github_issue(payload.title, body)
    except GithubIssueServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/candidates/{email_id}/reject", response_model=EmailResponse)
def reject_candidate(
    email_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().reject_candidate(email_id, payload, db)


@app.post("/candidates/{email_id}/track", response_model=EmailResponse)
def toggle_candidate_tracking(email_id: int, db: Session = Depends(get_db)) -> EmailResponse:
    email = _get_candidate_for_review(db, email_id)
    if email.state == "needs_review":
        _get_orchestration_service().set_tracking(email_id, not email.marked_for_tracking, db)
    elif email.state == "approved_sent":
        _require_applications_enabled(db)
        result = appts_service.create_tracked_application_from_email(db, email, owner_id=settings.owner_id)
        db.commit()
        if result and result[1]:
            appts_service.enqueue_embedding_generation(result[0].id)
    else:
        raise HTTPException(status_code=400, detail="Only needs_review or approved_sent candidates can be tracked")
    return _serialize_candidate_for_review(db, email)


@app.post("/candidates/{email_id}/send-to-failed-mapping", response_model=EmailResponse)
def send_to_failed_mapping(email_id: int, db: Session = Depends(get_db)) -> RecruiterEmail:
    return _get_orchestration_service().send_to_failed_mapping(email_id, db)


@app.post("/candidates/{email_id}/regenerate", response_model=EmailResponse)
def regenerate_candidate(
    email_id: int,
    payload: RegenerateCandidateRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _regenerate_single_candidate(email_id, payload, db)


def _regenerate_single_candidate(email_id: int, payload: RegenerateCandidateRequest, db: Session) -> RecruiterEmail:
    requested = _get_candidate_for_review(db, email_id)
    user_settings = _get_settings(db)
    if user_settings.feature_role_manifest_enabled and not requested.is_multi_role_child:
        if not payload.allow_role_manifest_fork:
            manifest_body = prepare_gmail_parse_body(requested.body) if requested.source == "gmail" else requested.body
            manifest_result = RoleManifestService(max_rung=4).detect(manifest_body)
            expansion = RequirementExpansionService().expand(
                db,
                requested,
                manifest_result,
                materialize=False,
            )
            if expansion.manifest_status == "multiple":
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "role_manifest_fork_required",
                        "requirement_count": expansion.requirement_count,
                    },
                )
            _retry_role_detection(requested.id, db, max_rung=4, manifest_result=manifest_result)
            return _get_candidate_for_review(db, requested.id)
        retry_role_detection(requested.id, db)
        return _get_candidate_for_review(db, requested.id)
    return _get_orchestration_service().regenerate_candidate(email_id, payload, db)


@app.post("/candidates/{email_id}/retry-role-detection", response_model=RoleDetectionRetryResponse)
def retry_role_detection(email_id: int, db: Session = Depends(get_db)) -> RoleDetectionRetryResponse:
    return _retry_role_detection(email_id, db, max_rung=4)


def _retry_role_detection(
    email_id: int,
    db: Session,
    *,
    max_rung: int,
    manifest_result=None,
) -> RoleDetectionRetryResponse:
    requested = _get_candidate_for_review(db, email_id)
    source = requested
    if requested.source_parent_email_id:
        source = _get_candidate_for_review(db, requested.source_parent_email_id)
    user_settings = _get_settings(db)
    manifest_body = prepare_gmail_parse_body(source.body) if source.source == "gmail" else source.body
    manifest_result = manifest_result or RoleManifestService(max_rung=max_rung).detect(manifest_body)
    expansion = RequirementExpansionService().expand(
        db,
        source,
        manifest_result,
        materialize=settings.role_manifest_child_creation_enabled,
    )

    processing_ids = list(expansion.child_ids)
    if manifest_result.status in {"single", "single_fallback"}:
        processing_ids = [source.id]
    if processing_ids:
        extract_and_score_children(
            db,
            processing_ids,
            user_settings=user_settings,
            get_candidate=_get_candidate_for_review,
            parse_email_with_details=parse_email_with_details,
            regenerate_candidate=_get_orchestration_service().regenerate_candidate,
        )
        rows = db.query(RecruiterEmail).filter(RecruiterEmail.id.in_(processing_ids)).all()
        for row in rows:
            row.semantic_embedding = None
            row.embedding_model = None
        db.commit()
        for candidate_id in processing_ids:
            _enqueue_embedding_generation(record_type="recruiter_email", record_id=candidate_id)

    return RoleDetectionRetryResponse(
        source_parent_id=expansion.source_parent_id,
        manifest_status=expansion.manifest_status,
        requirement_count=expansion.requirement_count,
        child_ids=list(expansion.child_ids),
    )


@app.delete("/candidates/{email_id}", response_model=dict[str, int | bool | str])
def dismiss_failed_candidate(email_id: int, db: Session = Depends(get_db)) -> dict[str, int | bool | str]:
    return _get_orchestration_service().dismiss_failed_candidate(email_id, db)


@app.post("/candidates/reject-bulk", response_model=BulkCandidateActionResponse)
def reject_bulk(payload: BulkRejectRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(
        payload.ids,
        lambda candidate_id: _get_orchestration_service().reject_candidate(
            candidate_id,
            RejectRequest(reason=payload.reason),
            db,
        ),
    )


@app.post("/candidates/approve-bulk", response_model=BulkCandidateActionResponse)
def approve_bulk_candidates(payload: BulkApproveRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    claim: BulkActionIdempotencyKey | None = None
    if payload.idempotency_key:
        db.query(BulkActionIdempotencyKey).filter(BulkActionIdempotencyKey.created_at < datetime.now(UTC) - timedelta(hours=24)).delete()
        db.commit()
        cached = db.get(BulkActionIdempotencyKey, (settings.owner_id, payload.idempotency_key))
        if cached is not None:
            if cached.response_json is None:
                raise HTTPException(status_code=409, detail="A request with this idempotency_key is already in progress")
            return BulkCandidateActionResponse.model_validate_json(cached.response_json)
        claim = BulkActionIdempotencyKey(owner_id=settings.owner_id, key=payload.idempotency_key, response_json=None)
        db.add(claim)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="A request with this idempotency_key is already in progress")

    succeeded_ids: list[int] = []
    failed: list[dict[str, object]] = []
    edited_replies = payload.edited_replies or {}
    for candidate_id in dict.fromkeys(payload.ids):
        try:
            _get_orchestration_service().approve_send(candidate_id, ApproveSendRequest(edited_reply=edited_replies.get(candidate_id)), db)
            _record_productivity_event(
                db,
                event_type="approved_sent",
                event_source="bulk_action",
                entity_id=candidate_id,
                entity_type="RecruiterEmail",
                metadata={},
            )
            succeeded_ids.append(candidate_id)
        except HTTPException as exc:
            failed.append({"id": candidate_id, "error": str(exc.detail)})
    response = BulkCandidateActionResponse(succeeded_ids=succeeded_ids, failed=failed)
    if claim is not None:
        claim.response_json = response.model_dump_json()
        db.commit()
    return response


@app.post("/candidates/regenerate-bulk", response_model=BulkCandidateActionResponse)
def regenerate_bulk_candidates(payload: BulkRegenerateRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(payload.ids, lambda candidate_id: _regenerate_single_candidate(candidate_id, RegenerateCandidateRequest(), db))


def _run_bulk(ids: list[int], action: Callable[[int], object]) -> BulkCandidateActionResponse:
    succeeded_ids: list[int] = []
    failed: list[dict[str, object]] = []
    for candidate_id in dict.fromkeys(ids):
        try:
            action(candidate_id)
            succeeded_ids.append(candidate_id)
        except HTTPException as exc:
            failed.append({"id": candidate_id, "error": str(exc.detail)})
    return BulkCandidateActionResponse(succeeded_ids=succeeded_ids, failed=failed)


@app.post("/candidates/track-bulk", response_model=BulkCandidateActionResponse)
def track_bulk(payload: BulkTrackRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(payload.ids, lambda candidate_id: _get_orchestration_service().set_tracking(candidate_id, payload.tracked, db))


@app.post("/candidates/send-to-failed-mapping-bulk", response_model=BulkCandidateActionResponse)
def send_to_failed_mapping_bulk(payload: BulkSendToFailedMappingRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(payload.ids, lambda candidate_id: _get_orchestration_service().send_to_failed_mapping(candidate_id, db))


@app.post("/candidates/resolve-recipients-bulk", response_model=BulkCandidateActionResponse)
def resolve_recipients_bulk(payload: BulkResolveRecipientsRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(list(payload.fixes), lambda candidate_id: _get_orchestration_service().resolve_recipients(candidate_id, payload.fixes[candidate_id], db))


@app.post("/candidates/delete-bulk", response_model=BulkCandidateActionResponse)
def delete_candidates_bulk(payload: BulkDeleteCandidatesRequest, db: Session = Depends(get_db)) -> BulkCandidateActionResponse:
    return _run_bulk(payload.ids, lambda candidate_id: _get_orchestration_service().dismiss_failed_candidate(candidate_id, db))


@app.post("/candidates/{email_id}/resolve-recipients", response_model=EmailResponse)
def resolve_recipients(
    email_id: int,
    payload: ResolveRecipientsRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    return _get_orchestration_service().resolve_recipients(email_id, payload, db)


# --- v3 relationship intelligence -------------------------------------------
#
# Ten routes, all owner-scoped on settings.owner_id. The judgment route is the
# only write path v3 exposes to the chat surface, and it is reached by a user's
# click on a rendered control - v3 registers no propose_* tool and no
# model-callable write.


def _require_relationship_intelligence() -> None:
    if not settings.feature_relationship_intelligence_enabled:
        raise HTTPException(status_code=404, detail="Not found")


@app.post("/taxonomy/entities/embed")
def embed_canonical_entities(
    entity_type: Annotated[str | None, Query()] = None,
    batch_size: Annotated[int, Query(ge=1, le=500)] = 300,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Backfill embeddings for approved canonical entities. Idempotent."""
    _require_relationship_intelligence()
    if entity_type:
        return {
            entity_type: entity_embedding_job.embed_pending_entities(
                db, owner_id=settings.owner_id, entity_type=entity_type, batch_size=batch_size
            )
        }
    return entity_embedding_job.embed_pending_entities_all_types(
        db, owner_id=settings.owner_id, batch_size=batch_size
    )


@app.get("/taxonomy/entities/alias-suggestions")
def list_entity_alias_suggestions(
    entity_type: Annotated[str, Query()] = "company",
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Entries that are probably the same entity. Suggestions only - never applied."""
    _require_relationship_intelligence()
    suggestions = entity_resolution_service.suggest_aliases(
        db, owner_id=settings.owner_id, entity_type=entity_type
    )
    rows = {
        int(row.id): row
        for row in db.query(CanonicalEntityTaxonomyEntry).filter(
            CanonicalEntityTaxonomyEntry.owner_id == settings.owner_id,
            CanonicalEntityTaxonomyEntry.id.in_([item for pair in suggestions for item in pair[:2]] or [0]),
        )
    }
    return {
        "entity_type": entity_type,
        "suggestions": [
            {
                "keep_id": keep_id,
                "keep_name": rows[keep_id].canonical_name if keep_id in rows else "",
                "keep_occurrences": int(rows[keep_id].occurrence_count or 0) if keep_id in rows else 0,
                "alias_id": alias_id,
                "alias_name": rows[alias_id].canonical_name if alias_id in rows else "",
                "alias_occurrences": int(rows[alias_id].occurrence_count or 0) if alias_id in rows else 0,
                "score": score,
            }
            for keep_id, alias_id, score in suggestions
        ],
    }


@app.post("/taxonomy/entities/aliases/merge")
def merge_entity_alias(
    payload: EntityAliasMergeRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Apply one reviewed merge. A wrong merge is invisible and permanent, so
    this is only ever reached from a human decision in the labeling tool."""
    _require_relationship_intelligence()
    try:
        return entity_resolution_service.apply_alias_merge(
            db,
            owner_id=settings.owner_id,
            entity_type=payload.entity_type,
            keep_id=payload.keep_id,
            alias_id=payload.alias_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/relationships/label-queue")
def get_relationship_label_queue(
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    hard_negative_ratio: Annotated[float, Query(ge=0.0, le=1.0)] = 0.4,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Unlabeled pairs to judge, with exactly the fields the scorer reads."""
    _require_relationship_intelligence()
    candidates = relationship_labeling_service.sample_pairs_for_labeling(
        db, owner_id=settings.owner_id, limit=limit, hard_negative_ratio=hard_negative_ratio
    )
    return {
        "verdicts": list(relationship_labeling_service.VERDICTS),
        "remaining": relationship_labeling_service.unlabeled_count(db, owner_id=settings.owner_id),
        "candidates": [candidate.as_dict() for candidate in candidates],
    }


@app.post("/relationships/labels")
def record_relationship_label(
    payload: RelationshipLabelRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_relationship_intelligence()
    try:
        row = relationship_labeling_service.record_label(
            db,
            owner_id=settings.owner_id,
            left_opportunity_id=payload.left_opportunity_id,
            right_opportunity_id=payload.right_opportunity_id,
            verdict=payload.verdict,
            reason=payload.reason,
            labeler=payload.labeler,
            sampler=payload.sampler,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": row.id, "verdict": row.verdict, "split": row.split}


@app.get("/relationships/labels/summary")
def get_relationship_label_summary(db: Session = Depends(get_db)) -> dict[str, object]:
    _require_relationship_intelligence()
    return relationship_labeling_service.label_summary(db, owner_id=settings.owner_id)


@app.post("/relationships/cluster-pass")
def run_relationship_cluster_pass(
    dry_run: Annotated[bool, Query()] = False,
    max_pairs: Annotated[int, Query(ge=1, le=100_000)] = 20_000,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Run one clustering pass on demand. Idempotent; shadow unless surfacing
    is enabled *and* the thresholds have been calibrated."""
    _require_relationship_intelligence()
    return relationship_clustering_service.run_clustering_pass(
        db, owner_id=settings.owner_id, dry_run=dry_run, max_pairs=max_pairs
    ).as_dict()


@app.get("/relationships/clusters/{cluster_id}")
def get_relationship_cluster(cluster_id: str, db: Session = Depends(get_db)) -> dict[str, object]:
    _require_relationship_intelligence()
    try:
        return relationship_judgment_service.cluster_detail(
            db, owner_id=settings.owner_id, cluster_id=cluster_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/relationships/opportunities/{opportunity_id}/clusters")
def get_clusters_for_opportunity(opportunity_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    _require_relationship_intelligence()
    return {
        "clusters": relationship_judgment_service.clusters_for_opportunity(
            db, owner_id=settings.owner_id, opportunity_id=opportunity_id
        )
    }


@app.post("/relationships/clusters/{cluster_id}/judgment")
def record_relationship_judgment(
    cluster_id: str,
    payload: RelationshipJudgmentRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Confirm, reject, or correct one inferred relationship.

    The only v3 write path. Out-of-scope and nonexistent clusters give the same
    404, so a response never confirms that another owner's cluster exists.
    """
    _require_relationship_intelligence()
    try:
        return relationship_judgment_service.record_judgment(
            db,
            owner_id=settings.owner_id,
            cluster_id=cluster_id,
            verdict=payload.verdict,
            note=payload.note,
            correct_member_ids=payload.correct_member_ids,
        ).as_dict()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- v4 scheduling ----------------------------------------------------------
#
# Nine routes, all owner-scoped on settings.owner_id, all 404 when the feature
# is off. Approval is a user's click on a rendered control - v4 registers no
# model-callable path that approves, sends, or changes a record.
#
# These sit BEFORE the catch-all mount below. Appending them after it would
# leave every one of them shadowed whenever chat is enabled, and no unit test
# would catch it because the routes exist in app.routes either way.


def _require_scheduling() -> None:
    if not settings.feature_scheduling_enabled:
        raise HTTPException(status_code=404, detail="Not found")


def _scheduling_error(exc: Exception) -> HTTPException:
    if isinstance(exc, task_service.TaskNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@app.get("/scheduled-tasks")
def list_scheduled_tasks_route(
    status: Annotated[str, Query()] = "all",
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Every task the user owns. No kind or flag may hide one from this list."""
    _require_scheduling()
    rows = task_service.list_tasks(db, owner_id=settings.owner_id, status=status)
    return {
        "tasks": [task_service.task_payload(db, row) for row in rows],
        "granularity_note": scheduling_schedule.granularity_note(),
    }


@app.get("/scheduled-tasks/pending-work")
def scheduled_pending_work(db: Session = Depends(get_db)) -> dict[str, object]:
    """One review surface: prepared runs and application suggestions together."""
    _require_scheduling()
    items = pending_work.pending_work(db, owner_id=settings.owner_id)
    return {"items": [item.as_dict() for item in items]}


@app.post("/scheduled-tasks")
def create_scheduled_task(
    payload: ScheduledTaskCreateRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_scheduling()
    try:
        task = task_service.create_task(
            db,
            owner_id=settings.owner_id,
            title=payload.title,
            kind=payload.kind,
            when=payload.when,
            note=payload.note,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            condition=payload.condition,
            action=payload.action,
            items=payload.items,
            retention_hours=payload.retention_hours,
        )
    except (task_service.TaskInvalid, task_service.TaskNotFound) as exc:
        raise _scheduling_error(exc) from exc
    return task_service.task_payload(db, task)


@app.patch("/scheduled-tasks/{task_id}")
def patch_scheduled_task(
    task_id: int,
    payload: ScheduledTaskPatchRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    _require_scheduling()
    try:
        task = task_service.patch_task(
            db,
            owner_id=settings.owner_id,
            task_id=task_id,
            operation=payload.operation,
            title=payload.title,
            when=payload.when,
            note=payload.note,
            condition=payload.condition,
            action=payload.action,
            retention_hours=payload.retention_hours,
        )
    except (task_service.TaskInvalid, task_service.TaskNotFound) as exc:
        raise _scheduling_error(exc) from exc
    return task_service.task_payload(db, task)


@app.delete("/scheduled-tasks/{task_id}")
def delete_scheduled_task(task_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    """Soft delete: the task stops running and its history stays readable."""
    _require_scheduling()
    try:
        task = task_service.delete_task(db, owner_id=settings.owner_id, task_id=task_id)
    except task_service.TaskNotFound as exc:
        raise _scheduling_error(exc) from exc
    return {"id": int(task.id), "status": task.status}


@app.get("/scheduled-tasks/{task_id}/runs")
def scheduled_task_runs(task_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    _require_scheduling()
    try:
        runs = task_service.task_runs(db, owner_id=settings.owner_id, task_id=task_id)
        items = task_service.task_items(db, owner_id=settings.owner_id, task_id=task_id)
    except task_service.TaskNotFound as exc:
        raise _scheduling_error(exc) from exc
    return {
        "runs": [task_service.run_payload(run) for run in runs],
        "items": [
            {
                "id": int(item.id),
                "position": int(item.position or 0),
                "text": item.text,
                "done": bool(item.done),
                "done_at": item.done_at.isoformat() if item.done_at else None,
            }
            for item in items
        ],
    }


@app.post("/scheduled-tasks/runs/{run_id}/approve")
def approve_scheduled_run(
    run_id: int,
    payload: ScheduledRunApproveRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Approve a prepared batch, in whole or in part.

    Refuses on a non-pending outcome AND on a fresh clock comparison against
    expires_at, so a stale open tab cannot approve work that expired an hour
    ago even if the sweep has not yet marked it.
    """
    _require_scheduling()
    try:
        result = task_service.approve_run(
            db,
            owner_id=settings.owner_id,
            run_id=run_id,
            item_ids=payload.item_ids,
            edits=payload.edits,
        )
    except (task_service.TaskInvalid, task_service.TaskNotFound) as exc:
        raise _scheduling_error(exc) from exc
    return {
        "run_id": result.run_id,
        "approved": result.approved,
        "failed": result.failed,
        "outcome": result.outcome,
        "errors": result.errors,
    }


@app.post("/scheduled-tasks/runs/{run_id}/discard")
def discard_scheduled_run(run_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    _require_scheduling()
    try:
        run = task_service.discard_run(db, owner_id=settings.owner_id, run_id=run_id)
    except (task_service.TaskInvalid, task_service.TaskNotFound) as exc:
        raise _scheduling_error(exc) from exc
    return task_service.run_payload(run)


@app.patch("/scheduled-tasks/{task_id}/items/{item_id}")
def patch_scheduled_task_item(
    task_id: int,
    item_id: int,
    payload: ScheduledTaskItemPatchRequest,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """A checkbox mutates nothing beyond the checklist, so it is a direct write."""
    _require_scheduling()
    try:
        item = task_service.patch_item(
            db,
            owner_id=settings.owner_id,
            task_id=task_id,
            item_id=item_id,
            done=payload.done,
            text=payload.text,
            position=payload.position,
        )
    except (task_service.TaskInvalid, task_service.TaskNotFound) as exc:
        raise _scheduling_error(exc) from exc
    return {
        "id": int(item.id),
        "position": int(item.position or 0),
        "text": item.text,
        "done": bool(item.done),
        "done_at": item.done_at.isoformat() if item.done_at else None,
    }


def _run_scheduling_sweep(db: Session) -> None:
    """The auto-runner callback. Enqueues into the worker's scheduled_task queue."""
    scheduling_sweep.run_scheduling_sweep(db, owner_id=settings.owner_id)


# Root mounting preserves FastMCP's exact /mcp endpoint. It must remain last so
# the existing FastAPI routes keep precedence over the catch-all mount.
if settings.feature_chat_enabled:
    app.mount("/", chat_mcp_app)

