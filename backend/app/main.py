import hashlib
import json
import logging
import math
import uuid
from collections.abc import Generator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, TypedDict, cast
import threading
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.config import settings
from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.ai.resume_context_attribution import (
    RESUME_CONTEXT_MISSING,
    RESUME_CONTEXT_RULES_ONLY,
)
from app.ai.resume_context import extract_resume_context
from app.cold_call import ColdCallContext, generate_cold_call_script
from app.automation import (
    RunOrchestrator,
    RunOrchestratorDependencies,
    RunOrchestratorRequest,
)
from app.db import Base, SessionLocal, engine, ensure_sqlite_phase0_columns
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
    start_oauth_bootstrap,
)
from app.gmail_labeling import GmailLabelingService, LabelRuleInput
from app.models import (
    DraftEditFeedback,
    EmployerNumber,
    NumberReviewQueue,
    PremiumNumberLead,
    ProductivityEvent,
    RecruiterEmail,
    RecruiterNumber,
    RecruiterOpportunity,
    ResumeAsset,
    SyncRun,
    UserSettings,
)
from app.models import RecipientRoutingFeedback
from app.telegram_bot import TelegramBotService, TelegramReply
from app.phase0 import (
    DEFAULT_FALLBACK_DRAFT_TEMPLATE,
    DEFAULT_SIGNATURE_EMAIL,
    DEFAULT_SIGNATURE_NAME,
    DEFAULT_SIGNATURE_PHONE,
    RoutingEvidence,
    RoutingResult,
    analyze_recipient_routing,
    ai_assist_score,
    draft_reply,
    email_domain,
    extract_email_address,
    greeting_from_to_contact,
    hard_filter_check,
    is_recruiter_like,
    normalize_employer_domains,
    parse_email,
    render_fallback_draft_template,
    requested_details_block,
    skills_from_text,
    should_block_f2f,
)
from app.routing import HeuristicRoutingAdapter, LearnedRoutingAdapter, RoutingDecision, RoutingPolicyInput, RoutingPolicyService
from app.premium_numbers import extract_and_store_premium_numbers
from app.premium_numbers.intelligence import OPPORTUNITY_STATUS_VALUES, process_email_number_intelligence
from app.premium_numbers.phone_normalization import canonicalize_phone
from app.query_bucket import sanitize_saved_queries
from app.runtime_state import runtime_state
from app.services import analytics_service, policy_service
from app.services.auto_runner_service import AutoRunnerService
from app.services.gmail_labeling_runtime_service import GmailLabelingRuntimeService
from app.services.orchestration_service import OrchestrationDeps, OrchestrationService
from app.services.settings_bootstrap_service import SettingsBootstrapService
from app.services.startup_service import StartupService
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.schemas import (
    AIStatusResponse,
    ApproveSendRequest,
    AutomationRunRequest,
    AutomationRunResponse,
    BulkRejectRequest,
    CandidateListResponse,
    EmailResponse,
    GmailStatusResponse,
    GmailSyncResponse,
    GmailLabelingPreviewRequest,
    GmailLabelingPreviewResponse,
    IngestEmailRequest,
    OAuthStartResponse,
    OAuthUrlResponse,
    EmployerNumberResponse,
    PremiumNumberListResponse,
    PremiumNumberResponse,
    RecruiterNumberResponse,
    RecruiterOpportunityPatchRequest,
    RecruiterOpportunityResponse,
    RejectRequest,
    ResolveRecipientsRequest,
    ResumeResponse,
    SettingsRequest,
    SettingsResponse,
    UnknownNumberReviewCardResponse,
    ProductivityEventCreateRequest,
    ProductivityEventResponse,
    ProductivityBarPoint,
    ProductivityTrendResponse,
    TelegramStatusResponse,
)
from app.semantic.embeddings_service import (
    begin_embedding_latency_capture,
    embedding_from_json,
    embedding_to_json,
    end_embedding_latency_capture,
    generate_embedding,
)
from app.semantic.ranking import blend_scores, semantic_similarity


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
telegram_service: TelegramBotService | None = runtime_state.telegram_service
telegram_runtime: TelegramRuntime | None = None
orchestration_service: OrchestrationService | None = None
auto_runner_service: AutoRunnerService | None = None
settings_bootstrap_service = SettingsBootstrapService(session_factory=SessionLocal)
gmail_labeling_runtime_service = GmailLabelingRuntimeService()
telegram_action_lock = runtime_state.telegram_action_lock
auto_runner_thread: threading.Thread | None = runtime_state.auto_runner_thread
auto_runner_stop_event = runtime_state.auto_runner_stop_event
gmail_labeling_service: GmailLabelingService | None = runtime_state.gmail_labeling_service


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
}

ALLOWED_VIEW_EVENTS = {
    "view_needs_review",
    "view_failed_mapping",
    "view_recent_runs",
    "view_sent_items",
    "view_run_queue",
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


def _get_auto_runner_service() -> AutoRunnerService:
    global auto_runner_service
    if auto_runner_service is None:
        auto_runner_service = AutoRunnerService(
            session_factory=SessionLocal,
            get_settings=_get_settings,
            run_once=automation_run_once,
            action_lock=telegram_action_lock,
            stop_event=auto_runner_stop_event,
        )
    return auto_runner_service


def _auto_runner_loop() -> None:
    _get_auto_runner_service().run_loop()


def _handle_telegram_command(chat_id: int, user_id: str, username: str, text: str) -> str | TelegramReply:
    if not telegram_runtime:
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


def _get_orchestration_service() -> OrchestrationService:
    global orchestration_service
    if orchestration_service is None:
        orchestration_service = OrchestrationService(
            OrchestrationDeps(
                owner_id=settings.owner_id,
                model_name=settings.deepseek_model_fast,
                get_settings=_get_settings,
                active_resume=_active_resume,
                effective_run_inputs=lambda user_settings, requested_mail_date: policy_service.effective_run_inputs(
                    gmail_query=user_settings.gmail_query,
                    default_gmail_query=user_settings.default_gmail_query,
                    default_date_mode=user_settings.default_date_mode,
                    policy_json=user_settings.policy_json,
                    saved_mail_date=user_settings.mail_date,
                    requested_mail_date=requested_mail_date,
                ),
                compute_blended_ai_score=_compute_blended_ai_score,
                analyze_email_routing=_analyze_email_routing,
                build_user_fallback_draft=_build_user_fallback_draft,
                apply_routing_result=_apply_routing_result,
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
                embedding_provider=lambda: (settings.semantic_embedding_provider or "hash").strip().lower(),
                embedding_model=lambda: settings.semantic_embedding_model or "text-embedding-3-small",
                routing_is_sendable=_routing_is_sendable,
                is_terminal_state=_is_terminal_state,
                email_domain=_email_domain,
                telegram_notify=lambda msg: telegram_service.notify(msg) if telegram_service else None,
                build_telegram_digest=_build_telegram_digest,
                set_last_gmail_sync_at=lambda ts: _set_last_gmail_sync_at(ts),
                set_ai_runtime=lambda vals: _set_ai_runtime(vals),
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
    if strictness == "lenient":
        return False, ""
    blocked, reason = should_block_f2f(parsed)
    if blocked:
        return True, reason
    if strictness == "strict":
        location_text = str(parsed.get("job_location_text", "")).strip().lower()
        if not location_text or location_text == "unknown":
            return True, "Location is unclear under strict location policy"
    return False, ""

def _active_resume(db: Session) -> ResumeAsset | None:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id, ResumeAsset.is_current.is_(True))
        .order_by(ResumeAsset.version.desc())
        .first()
    )


def _semantic_text_for_email(subject: str, body: str, role: str, skills_text: str) -> str:
    return "\n".join(
        [
            f"Subject: {subject or ''}",
            f"Role: {role or ''}",
            f"Skills: {skills_text or ''}",
            f"Body: {body or ''}",
        ]
    )


def _semantic_text_for_resume(resume: ResumeAsset | None) -> str:
    if not resume:
        return ""
    return extract_resume_context(resume.file_path, resume.file_name)


def _ensure_embedding_cached(current_payload: str | None, text: str) -> tuple[list[float], str | None, str]:
    cached = embedding_from_json(current_payload)
    if cached:
        return cached, current_payload, "cache"
    vector, provider = _generate_embedding_with_health(text)
    return vector, embedding_to_json(vector), provider


def _compute_blended_ai_score(
    *,
    subject: str,
    body: str,
    parsed: dict[str, str | int],
    user_settings: UserSettings,
    email_row: RecruiterEmail | None,
    resume: ResumeAsset | None,
) -> tuple[float, str, str, str | None, str | None]:
    keyword_score, keyword_summary = ai_assist_score(parsed, user_settings)
    if not user_settings.feature_semantic_enabled:
        return keyword_score, keyword_summary, "v1_rules_plus_ai", None, None

    try:
        email_text = _semantic_text_for_email(
            subject,
            body,
            str(parsed.get("role", "")),
            str(parsed.get("skills_text", "")),
        )
        resume_text = _semantic_text_for_resume(resume)
        if not resume_text.strip():
            return keyword_score, f"{keyword_summary}; semantic skipped (resume text unavailable)", "v2_rules_plus_semantic", None, None

        email_embedding, email_embedding_json, _ = _ensure_embedding_cached(
            email_row.semantic_embedding if email_row else None,
            email_text,
        )
        resume_embedding, resume_embedding_json, _ = _ensure_embedding_cached(
            resume.semantic_embedding if resume else None,
            resume_text,
        )
        similarity = semantic_similarity(email_embedding, resume_embedding)
        blended = blend_scores(
            keyword_score=keyword_score,
            semantic_similarity=similarity,
            semantic_enabled=True,
        )
        summary = f"{keyword_summary}; {blended.detail}"
        return blended.final_score, summary, blended.source, email_embedding_json, resume_embedding_json
    except Exception as exc:
        return keyword_score, f"{keyword_summary}; semantic fallback ({exc})", "v2_rules_plus_semantic_fallback", None, None


def _percentile_ms(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    index = min(len(ordered) - 1, rank - 1)
    return ordered[index]


def _is_terminal_state(email: RecruiterEmail) -> bool:
    return email.state in {"approved_sent", "rejected", "auto_rejected"}


def _email_domain(address: str) -> str:
    return email_domain(address)


def _learned_recipient_pairs(db: Session, sender: str) -> list[tuple[str, str]]:
    sender_domain = _email_domain(sender)
    if not sender_domain:
        return []
    feedback_rows = (
        db.query(RecipientRoutingFeedback)
        .filter(
            RecipientRoutingFeedback.owner_id == settings.owner_id,
            RecipientRoutingFeedback.sender_domain == sender_domain,
        )
        .order_by(RecipientRoutingFeedback.id.desc())
        .limit(25)
        .all()
    )
    return [(row.corrected_to, row.corrected_cc) for row in feedback_rows]


def _routing_payload_json(items: list[RoutingEvidence]) -> str:
    return json.dumps([asdict(item) for item in items])


def _apply_routing_result(email: RecruiterEmail, routing: RoutingResult) -> None:
    email.recipient_email = routing.to_email
    email.cc_email = routing.cc_email
    email.routing_status = routing.status
    email.routing_confidence = routing.confidence
    email.routing_reason = routing.reason
    email.routing_evidence = _routing_payload_json(routing.evidence)
    email.routing_candidates = _routing_payload_json(routing.candidates)


def _apply_routing_decision(email: RecruiterEmail, routing: RoutingDecision) -> None:
    email.recipient_email = routing.to_email
    email.cc_email = routing.cc_email
    email.routing_status = routing.status
    email.routing_confidence = routing.confidence
    email.routing_reason = routing.reason
    email.routing_evidence = _routing_payload_json(routing.evidence)
    email.routing_candidates = _routing_payload_json(routing.candidates)


def _capture_premium_numbers(db: Session, email: RecruiterEmail) -> None:
    try:
        extract_and_store_premium_numbers(db, email)
        process_email_number_intelligence(db, email)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.warning("Premium numbers extraction skipped for email_id=%s: %s", email.id, exc)


def _routing_is_sendable(email: RecruiterEmail) -> bool:
    decision = _evaluate_routing_policy(
        None,
        email.sender,
        email.subject,
        email.body,
        "",
        email.routing_confirmed,
        precomputed=RoutingResult(
            to_email=email.recipient_email,
            cc_email=email.cc_email,
            status=email.routing_status,
            confidence=float(email.routing_confidence or 0.0),
            reason=email.routing_reason,
            evidence=[],
            candidates=[],
        ),
    )
    return decision.is_sendable_candidate


def _analyze_email_routing(db: Session, sender: str, subject: str, body: str, snippet: str = "") -> RoutingResult:
    user_settings = _get_settings(db)
    return analyze_recipient_routing(
        sender,
        subject,
        body,
        snippet,
        learned_pairs=_learned_recipient_pairs(db, sender),
        employer_domains=_csv_to_list(user_settings.employer_domains),
    )


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
    if precomputed is not None:
        class _PrecomputedAdapter:
            def __init__(self, result: RoutingResult) -> None:
                self._result = result

            def evaluate(self, payload: RoutingPolicyInput) -> RoutingResult:
                _ = payload
                return self._result

        service = RoutingPolicyService(adapter=_PrecomputedAdapter(precomputed))
        return service.evaluate(
            RoutingPolicyInput(
                sender=sender,
                subject=subject,
                body=body,
                snippet=snippet,
                learned_pairs=[],
                routing_confirmed=routing_confirmed,
            )
        )

    learned_pairs = _learned_recipient_pairs(db, sender) if db else []
    employer_domains: list[str] | None = None
    if db is not None:
        user_settings = _get_settings(db)
        employer_domains = _csv_to_list(user_settings.employer_domains)
    adapter = LearnedRoutingAdapter(fallback=HeuristicRoutingAdapter())
    service = RoutingPolicyService(adapter=adapter)
    return service.evaluate(
        RoutingPolicyInput(
            sender=sender,
            subject=subject,
            body=body,
            snippet=snippet,
            learned_pairs=learned_pairs,
            employer_domains=employer_domains,
            routing_confirmed=routing_confirmed,
        )
    )


def _apply_draft_learning(db: Session, draft: str) -> str:
    _ = db
    return draft


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
    template = (user_settings.fallback_draft_template or DEFAULT_FALLBACK_DRAFT_TEMPLATE).strip()
    signature_name = (user_settings.signature_name or "").strip() or DEFAULT_SIGNATURE_NAME
    signature_phone = (user_settings.signature_phone or "").strip() or DEFAULT_SIGNATURE_PHONE
    signature_email = (user_settings.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL
    context = {
        "greeting": greeting_line,
        "role": role,
        "sender": sender,
        "location": str(parsed.get("location", "")),
        "salary_text": str(parsed.get("salary_text", "")),
        "skills_list": "\n".join(f"- {skill}" for skill in skills_from_text(str(parsed.get("skills_text", "")))),
        "skills_inline": ", ".join(skills_from_text(str(parsed.get("skills_text", "")))),
        "resume_file_name": resume_file_name or "",
        "signature_name": signature_name,
        "signature_phone": signature_phone,
        "signature_email": signature_email,
        "requested_details_block": requested_details_block(bool(parsed.get("asks_contact_fields", False))),
    }
    rendered = render_fallback_draft_template(template, context)
    if rendered.strip():
        return _apply_draft_learning(db, rendered)
    # Last resort resilience: keep old static generator if template is invalid/empty.
    return _apply_draft_learning(db, draft_reply(sender, role, parsed, greeting_line))


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
    effective_query: str | None = None,
    matched_count: int | None = None,
    queued_count: int | None = None,
    skipped_count: int | None = None,
    failed_count: int | None = None,
) -> AutomationRunResponse:
    if not email:
        return AutomationRunResponse(
            status=status,
            detail=detail,
            effective_query=effective_query,
            matched_count=matched_count,
            queued_count=queued_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )
    return AutomationRunResponse(
        status=status,
        detail=detail,
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
        feature_auto_send=s.feature_auto_send,
        feature_retry_queue=s.feature_retry_queue,
        feature_ai_enabled=s.feature_ai_enabled,
        feature_semantic_enabled=s.feature_semantic_enabled,
        fallback_draft_template=s.fallback_draft_template or DEFAULT_FALLBACK_DRAFT_TEMPLATE,
        signature_name=(s.signature_name or "").strip() or DEFAULT_SIGNATURE_NAME,
        signature_phone=(s.signature_phone or "").strip() or DEFAULT_SIGNATURE_PHONE,
        signature_email=(s.signature_email or "").strip() or DEFAULT_SIGNATURE_EMAIL,
        policy=policy,
        policy_profile_options=list(policy_service.policy_profiles().keys()),
        policy_profile_selected=policy_service.selected_policy_profile(policy),
        owner_id=s.owner_id,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _repair_unknown_role_drafts(db: Session, emails: list[RecruiterEmail]) -> None:
    changed = False
    user_settings = _get_settings(db)
    for email in emails:
        if email.state != "needs_review":
            continue
        if email.role != "Unknown Role" and "Unknown Role" not in email.draft_reply:
            continue
        parsed = parse_email(email.subject, email.body)
        role = str(parsed["role"])
        if role == "Unknown Role":
            continue
        email.role = role
        email.location = str(parsed["location"])
        email.salary_text = str(parsed["salary_text"])
        email.skills_text = str(parsed["skills_text"])
        if "Unknown Role" in email.draft_reply:
            greeting_line = greeting_from_to_contact(email.recipient_email, email.body)
            email.draft_reply = _build_user_fallback_draft(
                db,
                user_settings,
                sender=email.sender,
                role=role,
                parsed=parsed,
                greeting_line=greeting_line,
                resume_file_name=email.resume_file_name,
            )
            email.draft_source = "rules_only"
            email.draft_model = None
            email.draft_ai_error = None
            email.draft_resume_context_status = RESUME_CONTEXT_RULES_ONLY
        changed = True
    if changed:
        db.commit()


def _refresh_unconfirmed_routing(db: Session, emails: list[RecruiterEmail]) -> None:
    changed = False
    for email in emails:
        if email.source != "gmail" or email.routing_confirmed:
            continue
        routing = _evaluate_routing_policy(
            db,
            email.sender,
            email.subject,
            email.body,
            "",
            email.routing_confirmed,
        )
        if (
            email.recipient_email == routing.to_email
            and email.cc_email == routing.cc_email
            and email.routing_status == routing.status
            and float(email.routing_confidence or 0.0) == routing.confidence
        ):
            continue
        _apply_routing_decision(email, routing)
        if routing.should_mark_failed:
            email.state = routing.recommended_state
            email.last_error = "Could not resolve recruiter To and employer CC"
            email.skip_reason = routing.recommended_skip_reason
        changed = True
    if changed:
        db.commit()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/settings", response_model=SettingsResponse)
def get_settings(db: Session = Depends(get_db)) -> SettingsResponse:
    s = _get_settings(db)
    return _settings_response_from_model(s)


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
    s.feature_auto_send = payload.feature_auto_send
    s.feature_retry_queue = payload.feature_retry_queue
    s.feature_ai_enabled = payload.feature_ai_enabled
    s.feature_semantic_enabled = payload.feature_semantic_enabled
    s.fallback_draft_template = payload.fallback_draft_template.strip() if payload.fallback_draft_template.strip() else DEFAULT_FALLBACK_DRAFT_TEMPLATE
    s.signature_name = payload.signature_name.strip() if payload.signature_name.strip() else DEFAULT_SIGNATURE_NAME
    s.signature_phone = payload.signature_phone.strip() if payload.signature_phone.strip() else DEFAULT_SIGNATURE_PHONE
    s.signature_email = payload.signature_email.strip() if payload.signature_email.strip() else DEFAULT_SIGNATURE_EMAIL
    normalized_policy = policy_service.normalize_policy(
        payload.policy if payload.policy is not None else policy_service.read_policy_from_settings(s.policy_json)
    )
    s.policy_json = json.dumps(normalized_policy, separators=(",", ":"))
    db.commit()
    db.refresh(s)
    return _settings_response_from_model(s)


@app.post("/settings/resume", response_model=ResumeResponse)
def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ResumeResponse:
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
        is_current=True,
    )
    try:
        resume_text = _semantic_text_for_resume(resume)
        if resume_text.strip():
            resume_vector, _provider = _generate_embedding_with_health(resume_text)
            resume.semantic_embedding = embedding_to_json(resume_vector)
    except Exception as exc:
        logger.warning("Resume semantic embedding skipped: %s", exc)
    db.add(resume)
    db.commit()
    db.refresh(resume)
    return ResumeResponse.model_validate(resume)


@app.get("/settings/resumes", response_model=list[ResumeResponse])
def list_resumes(db: Session = Depends(get_db)) -> list[ResumeAsset]:
    return (
        db.query(ResumeAsset)
        .filter(ResumeAsset.owner_id == settings.owner_id)
        .order_by(ResumeAsset.version.desc())
        .all()
    )


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
def ai_status() -> AIStatusResponse:
    connected = bool(settings.deepseek_api_key)
    configured = connected and bool(settings.deepseek_base_url) and bool(settings.deepseek_model_fast)
    detail = "Ready" if connected else "DeepSeek API key missing (set Deepseek_API_KEY)."
    embedding_provider = (settings.semantic_embedding_provider or "hash").strip().lower()
    embedding_model = settings.semantic_embedding_model or "text-embedding-3-small"
    embedding_configured = False
    embedding_connected = False
    embedding_runtime_healthy: bool | None = None
    embedding_detail = "Embedding provider not configured."
    if embedding_provider == "hash":
        embedding_configured = True
        embedding_connected = True
        embedding_detail = "Ready (local hash embeddings)."
    elif embedding_provider == "openai":
        embedding_configured = bool(settings.openai_api_key)
        embedding_connected = embedding_configured
        embedding_detail = (
            "Ready"
            if embedding_configured
            else "OPENAI_API_KEY is missing for semantic embedding provider=openai."
        )
    elif embedding_provider == "openrouter":
        embedding_configured = bool(settings.openrouter_api_key) and bool(settings.openrouter_base_url)
        embedding_connected = embedding_configured
        embedding_detail = (
            "Ready"
            if embedding_configured
            else "OPENROUTER_API_KEY or OPENROUTER_BASE_URL is missing for semantic embedding provider=openrouter."
        )
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
        embedding_detail = "No runtime signal yet (no embedding attempts in this process)."

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
    event = _record_productivity_event(
        db,
        event_type=payload.event_type,
        event_source=payload.event_source or "ui",
        entity_id=payload.entity_id,
        metadata=payload.metadata,
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
    parsed = parse_email(payload.subject, payload.body)
    hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
    active_resume = _active_resume(db)
    ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json = _compute_blended_ai_score(
        subject=payload.subject,
        body=payload.body,
        parsed=parsed,
        user_settings=user_settings,
        email_row=None,
        resume=active_resume,
    )
    threshold = user_settings.qualification_threshold
    state = "needs_review" if hard_pass and ai_score >= threshold else "auto_rejected"
    decision = "Qualified" if state == "needs_review" else "Reject"
    fallback_draft = _build_user_fallback_draft(
        db,
        user_settings,
        sender=payload.sender,
        role=str(parsed["role"]),
        parsed=parsed,
        greeting_line=greeting_from_to_contact(None, payload.body),
        resume_file_name=active_resume.file_name if active_resume else None,
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
        score=int(ai_score * 100),
        decision=decision,
        state=state,
        decision_reason="manual_ingest",
        hard_filter_result=hard_reason,
        auto_reject_reason=None if state == "needs_review" else "manual_ingest_not_qualified",
        ai_score=ai_score,
        ai_score_source=ai_score_source,
        ai_summary=ai_summary,
        semantic_embedding=email_embedding_json,
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
    )
    db.add(email)
    if active_resume and resume_embedding_json and active_resume.semantic_embedding != resume_embedding_json:
        active_resume.semantic_embedding = resume_embedding_json
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
        query = query.filter(RecruiterEmail.source == "gmail")
        if field_name == "sent_at":
            query = query.filter(RecruiterEmail.sent_at.is_not(None))
            query = query.filter(RecruiterEmail.sent_at >= start, RecruiterEmail.sent_at < end)
        else:
            query = query.filter(RecruiterEmail.gmail_received_at.is_not(None))
            query = query.filter(RecruiterEmail.gmail_received_at >= start, RecruiterEmail.gmail_received_at < end)

    if sort == "highest_score":
        query = query.order_by(RecruiterEmail.score.desc(), RecruiterEmail.created_at.desc())
    else:
        query = query.order_by(RecruiterEmail.created_at.desc())

    items = query.offset(cursor).limit(limit + 1).all()
    has_next = len(items) > limit
    visible = items[:limit]
    _repair_unknown_role_drafts(db, visible)
    _refresh_unconfirmed_routing(db, visible)
    _fill_missing_gmail_rfc_ids(db, visible)
    next_cursor = cursor + limit if has_next else None
    return CandidateListResponse(
        items=[EmailResponse.model_validate(item) for item in visible],
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
    count = extract_and_store_premium_numbers(db, email)
    db.commit()
    return {"stored_count": count}


@app.get("/number-review", response_model=list[UnknownNumberReviewCardResponse])
def list_number_review_queue(db: Session = Depends(get_db)) -> list[UnknownNumberReviewCardResponse]:
    rows = (
        db.query(NumberReviewQueue)
        .filter(NumberReviewQueue.owner_id == settings.owner_id, NumberReviewQueue.state == "pending")
        .order_by(NumberReviewQueue.created_at.desc())
        .all()
    )
    return [UnknownNumberReviewCardResponse.model_validate(row) for row in rows]


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
            display_phone_number=card.display_phone_number,
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
                display_phone_number=card.display_phone_number,
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


@app.get("/recruiter-numbers", response_model=list[RecruiterNumberResponse])
def list_recruiter_numbers(db: Session = Depends(get_db)) -> list[RecruiterNumberResponse]:
    rows = (
        db.query(RecruiterNumber)
        .filter(RecruiterNumber.owner_id == settings.owner_id)
        .order_by(RecruiterNumber.updated_at.desc())
        .all()
    )
    results: list[RecruiterNumberResponse] = []
    for row in rows:
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
    return results


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
                display_phone_number=recruiter.display_phone_number,
                owner_name=recruiter.recruiter_name or "Unknown",
                company=recruiter.company or "Unknown",
                source_email_id=recruiter.first_detected_email_id,
            )
        )

    db.delete(recruiter)
    db.commit()
    return {"id": recruiter_number_id, "swapped_to": "employer"}


@app.get("/employer-numbers", response_model=list[EmployerNumberResponse])
def list_employer_numbers(db: Session = Depends(get_db)) -> list[EmployerNumberResponse]:
    rows = (
        db.query(EmployerNumber)
        .filter(EmployerNumber.owner_id == settings.owner_id)
        .order_by(EmployerNumber.updated_at.desc())
        .all()
    )
    return [EmployerNumberResponse.model_validate(row) for row in rows]


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
                display_phone_number=employer.display_phone_number,
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


@app.get("/recruiter-opportunities", response_model=list[RecruiterOpportunityResponse])
def list_recruiter_opportunities(
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    mail_date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    db: Session = Depends(get_db),
) -> list[RecruiterOpportunityResponse]:
    query = db.query(RecruiterOpportunity).filter(RecruiterOpportunity.owner_id == settings.owner_id)
    if status and status in OPPORTUNITY_STATUS_VALUES:
        query = query.filter(RecruiterOpportunity.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                RecruiterOpportunity.email_subject.ilike(like),
                RecruiterOpportunity.email_sender.ilike(like),
                RecruiterOpportunity.job_title.ilike(like),
                RecruiterOpportunity.client.ilike(like),
                RecruiterOpportunity.location.ilike(like),
                RecruiterOpportunity.extracted_skills.ilike(like),
            )
        )
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
    return [_recruiter_opportunity_response(row, recruiter_map.get(row.recruiter_number_id)) for row in rows]


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
        skills=row.extracted_skills or "",
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
def get_candidate(email_id: int, db: Session = Depends(get_db)) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return email


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
