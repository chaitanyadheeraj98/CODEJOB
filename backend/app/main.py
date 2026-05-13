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
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import settings
from app.ai.reply_service import generate_reply_with_ai_or_fallback
from app.ai.resume_context import extract_resume_context
from app.db import Base, SessionLocal, engine, ensure_sqlite_phase0_columns
from app.gmail_client import (
    get_message_rfc_message_id,
    gmail_auth_status,
    is_gmail_configured,
    list_unread_candidates_by_query,
    mark_message_processed,
    append_tracking_sheet_row,
    oauth_bootstrap_status,
    send_reply_with_attachment,
    start_oauth_bootstrap,
)
from app.models import DraftEditFeedback, ProductivityEvent, RecruiterEmail, ResumeAsset, SyncRun, UserSettings
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
    greeting_from_to_contact,
    hard_filter_check,
    is_recruiter_like,
    parse_email,
    render_fallback_draft_template,
    requested_details_block,
    skills_from_text,
    should_block_f2f,
)
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
    IngestEmailRequest,
    OAuthStartResponse,
    RejectRequest,
    ResolveRecipientsRequest,
    ResumeResponse,
    SettingsRequest,
    SettingsResponse,
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
    global auto_runner_thread, telegram_service
    Base.metadata.create_all(bind=engine)
    ensure_sqlite_phase0_columns()
    _ensure_default_settings()
    telegram_service = _init_telegram_service()
    auto_runner_stop_event.clear()
    auto_runner_thread = threading.Thread(target=_auto_runner_loop, name="mailops-auto-runner", daemon=True)
    auto_runner_thread.start()
    yield
    auto_runner_stop_event.set()
    if auto_runner_thread and auto_runner_thread.is_alive():
        auto_runner_thread.join(timeout=5.0)
    if telegram_service:
        telegram_service.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
logger = logging.getLogger(__name__)
last_gmail_sync_at: datetime | None = None
ai_running: bool = False
ai_last_error: str | None = None
ai_last_started_at: datetime | None = None
ai_last_finished_at: datetime | None = None
ai_last_duration_ms: int | None = None
telegram_service: TelegramBotService | None = None
telegram_action_lock = threading.Lock()
telegram_auth_sessions: dict[int, datetime] = {}
telegram_pending_inputs: dict[int, str] = {}
auto_runner_thread: threading.Thread | None = None
auto_runner_stop_event = threading.Event()

TELEGRAM_MENU_PAGE_SIZE = 6


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

RANGE_OPTIONS = {"last_1h", "current_day", "current_month", "current_year", "last_5y"}
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
    event = ProductivityEvent(
        owner_id=settings.owner_id,
        event_type=event_type,
        event_source=event_source,
        entity_id=entity_id,
        weight=EVENT_WEIGHTS.get(event_type, 0.0),
        metadata_json=json.dumps(dict(metadata or {})),
        occurred_at=occurred_at or datetime.now(UTC),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


def _event_response(event: ProductivityEvent) -> ProductivityEventResponse:
    parsed_metadata: dict[str, object] = {}
    try:
        payload = json.loads(event.metadata_json or "{}")
        if isinstance(payload, dict):
            parsed_metadata = cast(dict[str, object], payload)
    except json.JSONDecodeError:
        parsed_metadata = {}
    return ProductivityEventResponse(
        id=event.id,
        owner_id=event.owner_id,
        event_type=event.event_type,
        event_source=event.event_source,
        entity_id=event.entity_id,
        weight=event.weight,
        metadata=parsed_metadata,
        occurred_at=event.occurred_at,
        created_at=event.created_at,
    )


def _range_bounds(range_key: str) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    if range_key == "last_1h":
        return now - timedelta(hours=1), now
    if range_key == "current_day":
        start = datetime(now.year, now.month, now.day, tzinfo=UTC)
        return start, now
    if range_key == "current_month":
        start = datetime(now.year, now.month, 1, tzinfo=UTC)
        return start, now
    if range_key == "current_year":
        start = datetime(now.year, 1, 1, tzinfo=UTC)
        return start, now
    start = now - timedelta(days=365 * 5)
    return start, now


def _ensure_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _default_bucket_for_range(range_key: str) -> str:
    if range_key == "last_1h":
        return "five_min"
    if range_key == "current_day":
        return "hour"
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


def _parse_allowed_chat_ids(raw: str) -> set[int]:
    allowed: set[int] = set()
    for part in (raw or "").split(","):
        token = part.strip()
        if not token:
            continue
        try:
            allowed.add(int(token))
        except ValueError:
            logger.warning("Ignoring invalid TELEGRAM_ALLOWED_CHAT_IDS token: %s", token)
    return allowed


def _format_candidate_lines(rows: list[RecruiterEmail], max_items: int = 5) -> str:
    if not rows:
        return "None"
    lines: list[str] = []
    for row in rows[:max_items]:
        subject = (row.subject or "").strip().replace("\n", " ")
        if len(subject) > 90:
            subject = subject[:87] + "..."
        lines.append(f"#{row.id} - {subject}")
    return "\n".join(lines)


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
    resolved = _resolve_effective_run_inputs(user_settings, policy)
    date_mode = resolved["policy"]["query"]["date_mode"]
    effective_query = resolved["effective_query"]
    return (
        "Run preflight:\n"
        f"Saved query: {user_settings.gmail_query}\n"
        f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
        f"Default date mode: {user_settings.default_date_mode or 'today'}\n"
        f"Date mode: {date_mode}\n"
        f"Saved date: {user_settings.mail_date or 'any'}\n"
        f"Effective query: {effective_query}"
    )


def _extract_pin(parts: list[str]) -> tuple[list[str], str | None]:
    clean: list[str] = []
    pin: str | None = None
    for part in parts:
        if part.lower().startswith("pin="):
            pin = part.split("=", 1)[1].strip()
            continue
        clean.append(part)
    return clean, pin


def _is_valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _telegram_action_authorized(pin: str | None) -> bool:
    configured_pin = (settings.telegram_action_pin or "").strip()
    if not configured_pin:
        return True
    return bool(pin and pin == configured_pin)


def _telegram_ttl_minutes() -> int:
    return max(1, int(settings.telegram_auth_ttl_minutes or 30))


def _telegram_session_expires_at(chat_id: int) -> datetime | None:
    expires_at = telegram_auth_sessions.get(chat_id)
    if not expires_at:
        return None
    if expires_at <= datetime.now(UTC):
        telegram_auth_sessions.pop(chat_id, None)
        return None
    return expires_at


def _telegram_session_is_active(chat_id: int) -> bool:
    return _telegram_session_expires_at(chat_id) is not None


def _telegram_session_remaining(chat_id: int) -> str:
    expires_at = _telegram_session_expires_at(chat_id)
    if not expires_at:
        return "0m 0s"
    total = int((expires_at - datetime.now(UTC)).total_seconds())
    if total < 0:
        total = 0
    minutes = total // 60
    seconds = total % 60
    return f"{minutes}m {seconds}s"


def _tg_btn(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def _telegram_parse_callback_data(data: str) -> tuple[str, int]:
    parts = data.split(":", 2)
    action = parts[1] if len(parts) >= 2 else ""
    page = 0
    if len(parts) >= 3:
        try:
            page = max(0, int(parts[2]))
        except ValueError:
            page = 0
    return action, page


def _telegram_paginate_buttons(
    buttons: list[dict[str, str]],
    page: int,
    *,
    menu_action: str,
    include_home: bool = True,
    include_back: bool = False,
) -> list[list[dict[str, str]]]:
    total = len(buttons)
    start = page * TELEGRAM_MENU_PAGE_SIZE
    if start >= total:
        start = max(0, ((total - 1) // TELEGRAM_MENU_PAGE_SIZE) * TELEGRAM_MENU_PAGE_SIZE) if total else 0
    end = min(total, start + TELEGRAM_MENU_PAGE_SIZE)
    page_buttons = buttons[start:end]
    rows: list[list[dict[str, str]]] = [[button] for button in page_buttons]

    nav_row: list[dict[str, str]] = []
    if start > 0:
        nav_row.append(_tg_btn("Back", f"menu:{menu_action}:{(start // TELEGRAM_MENU_PAGE_SIZE) - 1}"))
    if end < total:
        nav_row.append(_tg_btn("More", f"menu:{menu_action}:{(start // TELEGRAM_MENU_PAGE_SIZE) + 1}"))
    if nav_row:
        rows.append(nav_row)

    foot_row: list[dict[str, str]] = []
    if include_back:
        foot_row.append(_tg_btn("Sections", "menu:main:0"))
    if include_home:
        foot_row.append(_tg_btn("Home", "menu:main:0"))
    if foot_row:
        rows.append(foot_row)
    return rows


def _telegram_main_menu_reply() -> TelegramReply:
    buttons = [
        _tg_btn("Read-only", "menu:readonly:0"),
        _tg_btn("Config", "menu:config:0"),
        _tg_btn("Actions", "menu:actions:0"),
        _tg_btn("Profile/Auth", "menu:profile:0"),
    ]
    return TelegramReply(
        text="MailOps bot is active. Choose a section:",
        inline_keyboard=[[button] for button in buttons],
    )


def _telegram_menu_reply(action: str, page: int = 0) -> TelegramReply:
    title = "Menu"
    buttons: list[dict[str, str]] = []
    if action == "readonly":
        title = "Read-only"
        buttons = [
            _tg_btn("Status", "cmd:/status"),
            _tg_btn("Needs Review", "cmd:/needs_review"),
            _tg_btn("Failed Mapping", "cmd:/failed_mapping"),
            _tg_btn("Recent Runs", "cmd:/recent_runs"),
        ]
    elif action == "config":
        title = "Config"
        buttons = [
            _tg_btn("Set Query", "flow:await_setquery"),
            _tg_btn("Set Date", "flow:await_setdate"),
            _tg_btn("Set Default Query", "flow:await_setdefaultquery"),
            _tg_btn("Set Default Date", "flow:await_setdefaultdate"),
            _tg_btn("Auto Run ON", "cmd:/setautorun on"),
            _tg_btn("Auto Run OFF", "cmd:/setautorun off"),
            _tg_btn("Set Auto Interval", "flow:await_setautointerval"),
        ]
    elif action == "actions":
        title = "Actions"
        buttons = [
            _tg_btn("Run", "cmd:/run"),
            _tg_btn("Sync", "cmd:/sync"),
            _tg_btn("Approve by ID", "flow:await_approve_id"),
            _tg_btn("Reject by ID", "flow:await_reject_id"),
        ]
    elif action == "profile":
        title = "Profile/Auth"
        buttons = [
            _tg_btn("Profile", "cmd:/profile"),
            _tg_btn("Authenticate", "flow:await_auth_pin"),
            _tg_btn("Logout", "cmd:/logout"),
            _tg_btn("Main Menu", "menu:main:0"),
        ]
    else:
        return _telegram_main_menu_reply()

    return TelegramReply(
        text=f"{title} menu:",
        inline_keyboard=_telegram_paginate_buttons(buttons, page, menu_action=action, include_back=True),
    )


def _telegram_pending_prompt(mode: str) -> str:
    prompts: dict[str, str] = {
        "await_setquery": "Send the new Gmail query text (or tap Cancel).",
        "await_setdate": "Send a date in YYYY-MM-DD or send `any` (or tap Cancel).",
        "await_setdefaultquery": "Send the new default Gmail query (or tap Cancel).",
        "await_setdefaultdate": "Send `today` or `off` (or tap Cancel).",
        "await_setautointerval": "Send the auto-run interval in minutes (1-1440).",
        "await_approve_id": "Send the email ID to approve (number only).",
        "await_reject_id": "Send the email ID to reject (number only).",
        "await_auth_pin": "Send your PIN to authenticate this chat session.",
    }
    return prompts.get(mode, "Send the required value.")


def _normalize_default_date_mode(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized not in {"today", "off"}:
        return "today"
    return normalized


def _resolve_effective_run_inputs(
    user_settings: UserSettings,
    policy: PolicyConfig,
    requested_mail_date: str | None = None,
) -> dict[str, object]:
    active_query = (user_settings.gmail_query or "").strip()
    default_query = (user_settings.default_gmail_query or "").strip()
    final_query = active_query or default_query or "is:unread in:inbox recruiter"

    explicit_mail_date = requested_mail_date or user_settings.mail_date
    default_date_mode = _normalize_default_date_mode(user_settings.default_date_mode)
    effective_mail_date = explicit_mail_date
    effective_policy = _normalize_policy(policy)
    if not effective_mail_date and default_date_mode == "today":
        effective_mail_date = datetime.now().date().isoformat()
        effective_policy["query"]["date_mode"] = "custom"

    effective_query = _compose_gmail_query(final_query, effective_mail_date, effective_policy)
    return {
        "query": final_query,
        "mail_date": effective_mail_date,
        "policy": effective_policy,
        "effective_query": effective_query,
    }


def _poll_interval_minutes(user_settings: UserSettings) -> int:
    return max(1, min(int(user_settings.feature_auto_poll_interval_minutes or 10), 1440))


def _auto_runner_loop() -> None:
    next_run_at = datetime.now(UTC)
    while not auto_runner_stop_event.wait(5):
        db = SessionLocal()
        try:
            user_settings = _get_settings(db)
            if not user_settings.enabled or not user_settings.feature_auto_polling:
                next_run_at = datetime.now(UTC)
                continue
            interval_minutes = _poll_interval_minutes(user_settings)
            now_utc = datetime.now(UTC)
            if now_utc < next_run_at:
                continue
            with telegram_action_lock:
                try:
                    result = automation_run_once(None, db)
                    logger.info(
                        "Auto runner completed: status=%s matched=%s queued=%s failed=%s",
                        result.status,
                        result.matched_count,
                        result.queued_count,
                        result.failed_count,
                    )
                except HTTPException as exc:
                    logger.warning("Auto runner skipped/failed: status=%s detail=%s", exc.status_code, exc.detail)
                except Exception:
                    logger.exception("Auto runner crashed during run-once")
            next_run_at = datetime.now(UTC) + timedelta(minutes=interval_minutes)
        except Exception:
            logger.exception("Auto runner loop error")
        finally:
            db.close()


def _handle_telegram_command(chat_id: int, user_id: str, username: str, text: str) -> str | TelegramReply:
    _ = chat_id
    _ = user_id
    _ = username
    command_line = text.strip()
    if not command_line:
        return "Empty command."
    if command_line.lower() == "/menu":
        return _telegram_main_menu_reply()

    pending_mode = telegram_pending_inputs.get(chat_id)
    if pending_mode and not command_line.startswith("/"):
        telegram_pending_inputs.pop(chat_id, None)
        if pending_mode == "await_setquery":
            return _handle_telegram_command(chat_id, user_id, username, f"/setquery {command_line}")
        if pending_mode == "await_setdate":
            return _handle_telegram_command(chat_id, user_id, username, f"/setdate {command_line}")
        if pending_mode == "await_setdefaultquery":
            return _handle_telegram_command(chat_id, user_id, username, f"/setdefaultquery {command_line}")
        if pending_mode == "await_setdefaultdate":
            return _handle_telegram_command(chat_id, user_id, username, f"/setdefaultdate {command_line}")
        if pending_mode == "await_setautointerval":
            return _handle_telegram_command(chat_id, user_id, username, f"/setautointerval {command_line}")
        if pending_mode == "await_approve_id":
            return _handle_telegram_command(chat_id, user_id, username, f"/approve {command_line}")
        if pending_mode == "await_reject_id":
            return _handle_telegram_command(chat_id, user_id, username, f"/reject {command_line} Rejected from Telegram")
        if pending_mode == "await_auth_pin":
            return _handle_telegram_command(chat_id, user_id, username, f"/auth {command_line}")

    parts = command_line.split()
    cmd = parts[0].lower()
    args, pin = _extract_pin(parts[1:])
    logger.info("Telegram command received chat_id=%s user=%s cmd=%s", chat_id, username, cmd)

    def require_action_auth() -> str | None:
        if _telegram_session_is_active(chat_id):
            return None
        if _telegram_action_authorized(pin):
            return None
        logger.info("Telegram auth denied chat_id=%s cmd=%s reason=missing_or_invalid_auth", chat_id, cmd)
        return "Action blocked. Run /auth <PIN> or provide pin=<PIN>."

    if cmd == "/start":
        return _telegram_main_menu_reply()

    db = SessionLocal()
    try:
        if cmd == "/auth":
            if not args:
                return "Usage: /auth <PIN>"
            configured_pin = (settings.telegram_action_pin or "").strip()
            if not configured_pin:
                telegram_auth_sessions[chat_id] = datetime.now(UTC) + timedelta(minutes=_telegram_ttl_minutes())
                logger.info("Telegram auth success chat_id=%s cmd=%s mode=no_configured_pin", chat_id, cmd)
                return f"Authenticated. Session expires in {_telegram_session_remaining(chat_id)}."
            supplied_pin = args[0].strip()
            if supplied_pin != configured_pin:
                logger.info("Telegram auth failed chat_id=%s cmd=%s reason=wrong_pin", chat_id, cmd)
                return "Authentication failed: incorrect PIN."
            telegram_auth_sessions[chat_id] = datetime.now(UTC) + timedelta(minutes=_telegram_ttl_minutes())
            logger.info("Telegram auth success chat_id=%s cmd=%s", chat_id, cmd)
            return f"Authenticated. Session expires in {_telegram_session_remaining(chat_id)}."

        if cmd == "/logout":
            telegram_auth_sessions.pop(chat_id, None)
            logger.info("Telegram logout chat_id=%s cmd=%s", chat_id, cmd)
            return "Logged out. Action commands now require /auth <PIN> or pin=<PIN>."

        if cmd == "/status":
            gmail_configured, gmail_authenticated, gmail_detail = gmail_auth_status()
            ai_info = ai_status()
            user_settings = _get_settings(db)
            policy = _read_policy_from_settings(user_settings)
            dry_run = _policy_dry_run(policy)
            is_authenticated = _telegram_session_is_active(chat_id)
            auth_line = f"Authenticated: {'yes' if is_authenticated else 'no'}"
            if is_authenticated:
                auth_line += f" (expires in {_telegram_session_remaining(chat_id)})"
            return (
                f"Gmail: {'Authenticated' if gmail_authenticated else 'Not authenticated'} "
                f"(configured={gmail_configured})\n"
                f"AI: {'Healthy' if ai_info.connected else 'Disconnected'} ({ai_info.model})\n"
                f"{auth_line}\n"
                f"Dry run: {dry_run}\n"
                f"Auto run: {'on' if user_settings.feature_auto_polling else 'off'} ({_poll_interval_minutes(user_settings)} min)\n"
                "Source: /run uses saved backend settings below.\n"
                f"Query: {user_settings.gmail_query}\n"
                f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
                f"Date: {user_settings.mail_date or 'any'}\n"
                f"Default date mode: {_normalize_default_date_mode(user_settings.default_date_mode)}\n"
                f"Detail: {gmail_detail}\n"
                "Hint: Use /setquery and /setdate to change what /run searches."
            )

        if cmd == "/profile":
            user_settings = _get_settings(db)
            return (
                "Profile defaults:\n"
                f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
                f"Default date mode: {_normalize_default_date_mode(user_settings.default_date_mode)}\n"
                f"Auto run: {'on' if user_settings.feature_auto_polling else 'off'} ({_poll_interval_minutes(user_settings)} min)\n"
                f"Active query: {user_settings.gmail_query}\n"
                f"Active date: {user_settings.mail_date or 'any'}"
            )

        if cmd == "/setquery":
            query_text = " ".join(args).strip()
            if not query_text:
                return "Usage: /setquery <gmail query>"
            user_settings = _get_settings(db)
            user_settings.gmail_query = query_text
            db.commit()
            logger.info("Telegram config update chat_id=%s field=gmail_query", chat_id)
            return f"Query updated to: {user_settings.gmail_query}"

        if cmd == "/setdefaultquery":
            query_text = " ".join(args).strip()
            if not query_text:
                return "Usage: /setdefaultquery <gmail query>"
            user_settings = _get_settings(db)
            user_settings.default_gmail_query = query_text
            db.commit()
            logger.info("Telegram config update chat_id=%s field=default_gmail_query", chat_id)
            return f"Default query updated to: {user_settings.default_gmail_query}"

        if cmd == "/setdate":
            if not args:
                return "Usage: /setdate YYYY-MM-DD or /setdate any"
            raw_value = args[0].strip().lower()
            user_settings = _get_settings(db)
            if raw_value in {"any", "clear", "none"}:
                user_settings.mail_date = None
                db.commit()
                logger.info("Telegram config update chat_id=%s field=mail_date value=any", chat_id)
                return "Mail date filter cleared. Runs will use any date."
            if not _is_valid_iso_date(raw_value):
                return "Invalid date. Use YYYY-MM-DD (example: /setdate 2026-05-08) or /setdate any."
            user_settings.mail_date = raw_value
            db.commit()
            logger.info("Telegram config update chat_id=%s field=mail_date value=%s", chat_id, raw_value)
            return f"Mail date set to: {raw_value}"

        if cmd == "/setdefaultdate":
            if not args:
                return "Usage: /setdefaultdate today|off"
            mode = (args[0] or "").strip().lower()
            if mode not in {"today", "off"}:
                return "Invalid mode. Use /setdefaultdate today or /setdefaultdate off."
            user_settings = _get_settings(db)
            user_settings.default_date_mode = mode
            db.commit()
            logger.info("Telegram config update chat_id=%s field=default_date_mode value=%s", chat_id, mode)
            return f"Default date mode set to: {mode}"

        if cmd == "/setautorun":
            if not args:
                return "Usage: /setautorun on|off"
            mode = (args[0] or "").strip().lower()
            if mode not in {"on", "off"}:
                return "Invalid mode. Use /setautorun on or /setautorun off."
            user_settings = _get_settings(db)
            user_settings.feature_auto_polling = mode == "on"
            db.commit()
            logger.info("Telegram config update chat_id=%s field=feature_auto_polling value=%s", chat_id, mode)
            return f"Auto run set to: {mode} (interval={_poll_interval_minutes(user_settings)} min)"

        if cmd == "/setautointerval":
            if not args:
                return "Usage: /setautointerval <minutes>"
            try:
                minutes = int(args[0])
            except ValueError:
                return "Invalid interval. Use /setautointerval <minutes>."
            minutes = max(1, min(minutes, 1440))
            user_settings = _get_settings(db)
            user_settings.feature_auto_poll_interval_minutes = minutes
            db.commit()
            logger.info("Telegram config update chat_id=%s field=feature_auto_poll_interval_minutes value=%s", chat_id, minutes)
            return f"Auto run interval set to: {minutes} minute(s)."

        if cmd == "/needs_review":
            rows = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.state == "needs_review")
                .order_by(RecruiterEmail.created_at.desc())
                .limit(5)
                .all()
            )
            count = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.state == "needs_review")
                .count()
            )
            return f"Needs Review: {count}\nTop items:\n{_format_candidate_lines(rows)}"

        if cmd == "/failed_mapping":
            rows = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.state == "failed")
                .order_by(RecruiterEmail.created_at.desc())
                .limit(5)
                .all()
            )
            count = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.state == "failed")
                .count()
            )
            return f"Failed Mapping: {count}\nTop items:\n{_format_candidate_lines(rows)}"

        if cmd == "/recent_runs":
            runs = (
                db.query(SyncRun)
                .filter(SyncRun.owner_id == settings.owner_id)
                .order_by(SyncRun.created_at.desc())
                .limit(5)
                .all()
            )
            if not runs:
                return "No recent runs."
            lines = [
                f"{run.created_at.isoformat()} | imported={run.imported_count} skipped={run.skipped_count} errors={run.error_count}"
                for run in runs
            ]
            return "Recent runs:\n" + "\n".join(lines)

        if cmd == "/sync":
            auth_error = require_action_auth()
            if auth_error:
                return auth_error
            with telegram_action_lock:
                result = gmail_sync(db)
            return (
                "Advanced sync finished (import-only, no queue/send).\n"
                f"Batch: {result.sync_batch_id}\n"
                f"Imported: {result.imported_count} | Skipped: {result.skipped_count} | Errors: {result.error_count}"
            )

        if cmd == "/run":
            auth_error = require_action_auth()
            if auth_error:
                return auth_error
            user_settings = _get_settings(db)
            policy = _read_policy_from_settings(user_settings)
            preflight = _format_query_preflight(user_settings, policy)
            with telegram_action_lock:
                run_result = automation_run_once(None, db)
            if run_result.status == "idle":
                return (
                    f"{preflight}\n\n"
                    f"{_build_telegram_digest('Run finished.', run_result)}\n\n"
                    "Guidance: No matches for saved query/date.\n"
                    "Try: /setquery <gmail query>\n"
                    "Try: /setdate YYYY-MM-DD or /setdate any"
                )
            return f"{preflight}\n\n{_build_telegram_digest('Run finished.', run_result)}"

        if cmd == "/approve":
            auth_error = require_action_auth()
            if auth_error:
                return auth_error
            if not args:
                return "Usage: /approve <email_id>"
            try:
                email_id = int(args[0])
            except ValueError:
                return "Invalid email_id. Usage: /approve <email_id>"
            with telegram_action_lock:
                email = approve_and_send(email_id, ApproveSendRequest(edited_reply=None), db)
            return f"Approved and sent: #{email.id} | {email.subject}"

        if cmd == "/reject":
            auth_error = require_action_auth()
            if auth_error:
                return auth_error
            if not args:
                return "Usage: /reject <email_id> [reason...]"
            try:
                email_id = int(args[0])
            except ValueError:
                return "Invalid email_id. Usage: /reject <email_id> [reason...]"
            reason = " ".join(args[1:]).strip() or "Rejected from Telegram"
            with telegram_action_lock:
                email = reject_candidate(email_id, RejectRequest(reason=reason), db)
            return f"Rejected: #{email.id} | reason={email.decision_reason or reason}"

        reply = TelegramReply(
            text="Unknown command. Use Menu below (typed /commands still work).",
            inline_keyboard=[[_tg_btn("Open Menu", "menu:main:0")]],
        )
        logger.info("Telegram command result chat_id=%s cmd=%s result=unknown_command", chat_id, cmd)
        return reply
    except HTTPException as exc:
        logger.info("Telegram command result chat_id=%s cmd=%s result=http_error_%s", chat_id, cmd, exc.status_code)
        return f"Command failed ({exc.status_code}): {exc.detail}"
    except Exception as exc:
        logger.exception("Telegram command error")
        logger.info("Telegram command result chat_id=%s cmd=%s result=exception", chat_id, cmd)
        return f"Command failed: {exc}"
    finally:
        db.close()


def _handle_telegram_callback(
    chat_id: int,
    user_id: str,
    username: str,
    callback_data: str,
    message_id: int,
) -> str | TelegramReply:
    _ = user_id
    _ = username
    action, page = _telegram_parse_callback_data(callback_data)

    if callback_data.startswith("menu:"):
        reply = _telegram_menu_reply(action, page)
        reply.edit_message_id = message_id
        reply.callback_notice = "Updated."
        return reply

    if callback_data == "cancel:pending":
        telegram_pending_inputs.pop(chat_id, None)
        reply = _telegram_main_menu_reply()
        reply.edit_message_id = message_id
        reply.callback_notice = "Canceled."
        return reply

    if callback_data.startswith("flow:"):
        mode = callback_data.split(":", 1)[1].strip()
        telegram_pending_inputs[chat_id] = mode
        return TelegramReply(
            text=_telegram_pending_prompt(mode),
            inline_keyboard=[[_tg_btn("Cancel", "cancel:pending")], [_tg_btn("Home", "menu:main:0")]],
            edit_message_id=message_id,
            callback_notice="Awaiting input.",
        )

    if callback_data.startswith("cmd:"):
        command_text = callback_data.split(":", 1)[1].strip()
        result = _handle_telegram_command(chat_id, user_id, username, command_text)
        if isinstance(result, TelegramReply):
            if result.edit_message_id is None:
                result.edit_message_id = message_id
            if result.callback_notice is None:
                result.callback_notice = "Done."
            return result
        return TelegramReply(
            text=result,
            inline_keyboard=[[_tg_btn("Back", "menu:main:0")]],
            edit_message_id=message_id,
            callback_notice="Done.",
        )

    return TelegramReply(
        text="Unknown action. Opening main menu.",
        inline_keyboard=_telegram_main_menu_reply().inline_keyboard,
        edit_message_id=message_id,
        callback_notice="Unknown action.",
    )


def _init_telegram_service() -> TelegramBotService | None:
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return None
    allowed_chat_ids = _parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)
    if not allowed_chat_ids:
        logger.warning("Telegram bot token exists but TELEGRAM_ALLOWED_CHAT_IDS is empty. Bot will not start.")
        return None
    service = TelegramBotService(
        token=token,
        allowed_chat_ids=allowed_chat_ids,
        alerts_enabled=settings.telegram_alerts_enabled,
        command_handler=_handle_telegram_command,
        callback_handler=_handle_telegram_callback,
    )
    service.start()
    logger.info("Telegram bot started with %s authorized chat(s)", len(allowed_chat_ids))
    return service


def _ensure_default_settings() -> None:
    db = SessionLocal()
    try:
        existing = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
        if existing:
            if not existing.policy_json:
                existing.policy_json = json.dumps(_default_policy(), separators=(",", ":"))
            if not existing.fallback_draft_template:
                existing.fallback_draft_template = DEFAULT_FALLBACK_DRAFT_TEMPLATE
            if not existing.signature_name:
                existing.signature_name = DEFAULT_SIGNATURE_NAME
            if not existing.signature_phone:
                existing.signature_phone = DEFAULT_SIGNATURE_PHONE
            if not existing.signature_email:
                existing.signature_email = DEFAULT_SIGNATURE_EMAIL
            if not (existing.default_gmail_query or "").strip():
                existing.default_gmail_query = (existing.gmail_query or "").strip() or "is:unread in:inbox recruiter"
            existing.default_date_mode = _normalize_default_date_mode(existing.default_date_mode)
            existing.feature_auto_poll_interval_minutes = _poll_interval_minutes(existing)
            if (
                not existing.policy_json
                or not existing.fallback_draft_template
                or not existing.signature_name
                or not existing.signature_phone
                or not existing.signature_email
                or not (existing.default_gmail_query or "").strip()
            ):
                db.commit()
            return
        default_settings = UserSettings(
            owner_id=settings.owner_id,
            enabled=True,
            gmail_query="is:unread in:inbox recruiter",
            default_gmail_query="is:unread in:inbox recruiter",
            mail_date=None,
            default_date_mode="today",
            qualification_threshold=settings.qualification_threshold,
            feature_auto_polling=settings.feature_auto_polling,
            feature_auto_poll_interval_minutes=max(1, int(settings.feature_auto_poll_interval_minutes or 10)),
            feature_auto_send=settings.feature_auto_send,
            feature_retry_queue=settings.feature_retry_queue,
            feature_ai_enabled=False,
            feature_semantic_enabled=False,
            fallback_draft_template=DEFAULT_FALLBACK_DRAFT_TEMPLATE,
            signature_name=DEFAULT_SIGNATURE_NAME,
            signature_phone=DEFAULT_SIGNATURE_PHONE,
            signature_email=DEFAULT_SIGNATURE_EMAIL,
            policy_json=json.dumps(_default_policy()),
        )
        db.add(default_settings)
        db.commit()
    finally:
        db.close()


def _get_settings(db: Session) -> UserSettings:
    user_settings = db.query(UserSettings).filter(UserSettings.owner_id == settings.owner_id).first()
    if not user_settings:
        raise HTTPException(status_code=500, detail="Settings not initialized")
    return user_settings


def _to_csv(values: list[str]) -> str:
    return ",".join(v.strip() for v in values if v.strip())


def _default_policy() -> PolicyConfig:
    return {
        "version": 1,
        "query": {
            "force_unread": True,
            "include_labels": [],
            "exclude_labels": [],
            "date_mode": "custom",
        },
        "run": {
            "run_mode": "all",
            "batch_limit": 20,
            "dry_run": False,
        },
        "qualification": {
            "location_strictness": "balanced",
            "score_threshold_override_enabled": False,
            "score_threshold_override_value": 0.6,
        },
    }


def _policy_profiles() -> dict[str, PolicyConfig]:
    return {
        "Aggressive": _normalize_policy(
            {
                "version": 1,
                "query": {
                    "force_unread": True,
                    "include_labels": [],
                    "exclude_labels": [],
                    "date_mode": "any",
                },
                "run": {
                    "run_mode": "all",
                    "batch_limit": 100,
                    "dry_run": False,
                },
                "qualification": {
                    "location_strictness": "lenient",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.50,
                },
            }
        ),
        "Balanced": _normalize_policy(_default_policy()),
        "Strict": _normalize_policy(
            {
                "version": 1,
                "query": {
                    "force_unread": True,
                    "include_labels": [],
                    "exclude_labels": [],
                    "date_mode": "custom",
                },
                "run": {
                    "run_mode": "all",
                    "batch_limit": 10,
                    "dry_run": False,
                },
                "qualification": {
                    "location_strictness": "strict",
                    "score_threshold_override_enabled": True,
                    "score_threshold_override_value": 0.75,
                },
            }
        ),
    }


def _selected_policy_profile(policy: PolicyConfig) -> str | None:
    normalized = _normalize_policy(policy)
    for profile_name, profile_policy in _policy_profiles().items():
        if normalized == profile_policy:
            return profile_name
    return None


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict):
        return cast(Mapping[str, object], value)
    return {}


def _as_str(value: object, default: str) -> str:
    return str(value).strip() if value is not None else default


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _normalize_policy(raw_policy: object) -> PolicyConfig:
    default_policy = _default_policy()
    if not isinstance(raw_policy, Mapping):
        return default_policy

    raw_policy_map = _as_mapping(raw_policy)
    query_raw = raw_policy_map.get("query")
    run_raw = raw_policy_map.get("run")
    qualification_raw = raw_policy_map.get("qualification")

    query = _as_mapping(query_raw)
    run = _as_mapping(run_raw)
    qualification = _as_mapping(qualification_raw)

    date_mode_raw = query.get("date_mode")
    date_mode = _as_str(date_mode_raw, "custom")
    if date_mode not in {"custom", "any"}:
        date_mode = "custom"

    run_mode_raw = run.get("run_mode")
    run_mode = _as_str(run_mode_raw, "all")
    if run_mode not in {"all"}:
        run_mode = "all"

    batch_limit = _as_int(run.get("batch_limit"), 20)
    batch_limit = max(1, min(batch_limit, 200))

    score_override = _as_float(qualification.get("score_threshold_override_value"), 0.6)
    score_override = max(0.0, min(score_override, 1.0))

    location_raw = qualification.get("location_strictness")
    location_strictness = _as_str(location_raw, "balanced")
    if location_strictness not in {"lenient", "balanced", "strict"}:
        location_strictness = "balanced"

    return {
        "version": 1,
        "query": {
            "force_unread": bool(query.get("force_unread", True)),
            "include_labels": _as_string_list(query.get("include_labels", [])),
            "exclude_labels": _as_string_list(query.get("exclude_labels", [])),
            "date_mode": date_mode,
        },
        "run": {
            "run_mode": run_mode,
            "batch_limit": batch_limit,
            "dry_run": bool(run.get("dry_run", False)),
        },
        "qualification": {
            "location_strictness": location_strictness,
            "score_threshold_override_enabled": bool(qualification.get("score_threshold_override_enabled", False)),
            "score_threshold_override_value": score_override,
        },
    }


def _read_policy_from_settings(user_settings: UserSettings) -> PolicyConfig:
    if not user_settings.policy_json:
        return _default_policy()
    try:
        parsed = json.loads(user_settings.policy_json)
    except json.JSONDecodeError:
        return _default_policy()
    return _normalize_policy(parsed)


def _policy_threshold(user_settings: UserSettings, policy: PolicyConfig) -> float:
    normalized = _normalize_policy(policy)
    qualification = normalized["qualification"]
    if bool(qualification.get("score_threshold_override_enabled", False)):
        value = _as_float(
            qualification.get("score_threshold_override_value", user_settings.qualification_threshold),
            user_settings.qualification_threshold,
        )
        return max(0.0, min(value, 1.0))
    return user_settings.qualification_threshold


def _policy_f2f_block(parsed: dict[str, str | int | bool], policy: PolicyConfig) -> tuple[bool, str]:
    normalized = _normalize_policy(policy)
    qualification = normalized["qualification"]
    strictness = _as_str(qualification.get("location_strictness", "balanced"), "balanced")
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


def _policy_batch_limit(policy: PolicyConfig, default_value: int = 20) -> int:
    normalized = _normalize_policy(policy)
    run_policy = normalized["run"]
    value = _as_int(run_policy.get("batch_limit", default_value), default_value)
    return max(1, min(value, 200))


def _policy_dry_run(policy: PolicyConfig) -> bool:
    normalized = _normalize_policy(policy)
    run_policy = normalized["run"]
    return bool(run_policy.get("dry_run", False))


def _compose_gmail_query(base_query: str, mail_date: str | None = None, policy: PolicyConfig | None = None) -> str:
    policy_obj = _normalize_policy(policy)
    query_section = policy_obj["query"]

    parts = [base_query.strip()]
    if bool(query_section.get("force_unread", True)):
        parts.append("is:unread")

    for label in _as_string_list(query_section.get("include_labels", [])):
        parts.append(f"label:{label}")
    for label in _as_string_list(query_section.get("exclude_labels", [])):
        parts.append(f"-label:{label}")

    date_mode = _as_str(query_section.get("date_mode", "custom"), "custom")
    if mail_date and date_mode == "custom":
        selected = date.fromisoformat(mail_date)
        next_day = selected + timedelta(days=1)
        parts.append(f"after:{selected.strftime('%Y/%m/%d')}")
        parts.append(f"before:{next_day.strftime('%Y/%m/%d')}")
    return " ".join(part for part in parts if part)


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
    vector, provider = generate_embedding(text)
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


def _routing_is_sendable(email: RecruiterEmail) -> bool:
    if email.routing_confirmed:
        return True
    return email.routing_status in {"safe", "confirmed"} and email.routing_confidence >= 0.8


def _analyze_email_routing(db: Session, sender: str, subject: str, body: str, snippet: str = "") -> RoutingResult:
    return analyze_recipient_routing(
        sender,
        subject,
        body,
        snippet,
        learned_pairs=_learned_recipient_pairs(db, sender),
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
        effective_query=effective_query,
        matched_count=matched_count,
        queued_count=queued_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
    )


def _settings_response_from_model(s: UserSettings) -> SettingsResponse:
    policy = _read_policy_from_settings(s)
    return SettingsResponse(
        enabled=s.enabled,
        gmail_query=s.gmail_query,
        default_gmail_query=(s.default_gmail_query or "").strip() or (s.gmail_query or "").strip() or "is:unread in:inbox recruiter",
        mail_date=s.mail_date,
        default_date_mode=_normalize_default_date_mode(s.default_date_mode),
        min_salary=s.min_salary,
        accepted_locations=[v for v in s.accepted_locations.split(",") if v],
        visa_required_allowed=s.visa_required_allowed,
        remote_preference=s.remote_preference,
        role_keywords=[v for v in s.role_keywords.split(",") if v],
        must_have_skills=[v for v in s.must_have_skills.split(",") if v],
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
        policy_profile_options=list(_policy_profiles().keys()),
        policy_profile_selected=_selected_policy_profile(policy),
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
        changed = True
    if changed:
        db.commit()


def _refresh_unconfirmed_routing(db: Session, emails: list[RecruiterEmail]) -> None:
    changed = False
    for email in emails:
        if email.source != "gmail" or email.routing_confirmed:
            continue
        routing = _analyze_email_routing(db, email.sender, email.subject, email.body)
        if (
            email.recipient_email == routing.to_email
            and email.cc_email == routing.cc_email
            and email.routing_status == routing.status
            and float(email.routing_confidence or 0.0) == routing.confidence
        ):
            continue
        _apply_routing_result(email, routing)
        if routing.status == "missing":
            email.state = "failed"
            email.last_error = "Could not resolve recruiter To and employer CC"
            email.skip_reason = "missing_to_or_cc"
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
    s.mail_date = payload.mail_date
    s.default_date_mode = _normalize_default_date_mode(payload.default_date_mode)
    s.min_salary = payload.min_salary
    s.accepted_locations = _to_csv(payload.accepted_locations)
    s.visa_required_allowed = payload.visa_required_allowed
    s.remote_preference = payload.remote_preference
    s.role_keywords = _to_csv(payload.role_keywords)
    s.must_have_skills = _to_csv(payload.must_have_skills)
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
    normalized_policy = _normalize_policy(payload.policy if payload.policy is not None else _read_policy_from_settings(s))
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
            resume_vector, _provider = generate_embedding(resume_text)
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
    return AIStatusResponse(
        configured=configured,
        connected=connected,
        running=ai_running,
        provider="deepseek",
        model=settings.deepseek_model_fast or "deepseek-chat",
        detail=detail,
        last_error=ai_last_error,
        last_started_at=ai_last_started_at,
        last_finished_at=ai_last_finished_at,
        last_duration_ms=ai_last_duration_ms,
        last_draft_source=ai_last_draft_source,
    )


@app.get("/telegram/status", response_model=TelegramStatusResponse)
def telegram_status() -> TelegramStatusResponse:
    if not telegram_service:
        configured_ids = _parse_allowed_chat_ids(settings.telegram_allowed_chat_ids)
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
    global last_gmail_sync_at
    if not is_gmail_configured():
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")

    user_settings = _get_settings(db)
    if not user_settings.enabled:
        raise HTTPException(status_code=400, detail="Pipeline is disabled in settings")

    sync_batch_id = str(uuid.uuid4())
    sync_run = SyncRun(owner_id=settings.owner_id, sync_batch_id=sync_batch_id, started_at=datetime.now(UTC))
    db.add(sync_run)
    db.commit()

    imported_count = 0
    skipped_count = 0
    error_count = 0
    try:
        policy = _read_policy_from_settings(user_settings)
        resolved = _resolve_effective_run_inputs(user_settings, policy, None)
        effective_query = str(resolved["effective_query"])
        candidates = list_unread_candidates_by_query(effective_query)
        for item in candidates:
            existing = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id)
                .filter(RecruiterEmail.external_message_id == item["external_message_id"])
                .first()
            )
            if existing:
                skipped_count += 1
                continue

            if not is_recruiter_like(item["sender"], item["subject"], item["body"]):
                skipped_count += 1
                continue

            parsed = parse_email(item["subject"], item["body"])
            hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
            active_resume = _active_resume(db)
            ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json = _compute_blended_ai_score(
                subject=item["subject"],
                body=item["body"],
                parsed=parsed,
                user_settings=user_settings,
                email_row=None,
                resume=active_resume,
            )
            threshold = user_settings.qualification_threshold

            state = "needs_review"
            decision = "Qualified"
            decision_reason = "Qualified by hard filters + AI score"
            auto_reject_reason = None
            draft = ""

            if not hard_pass:
                state = "auto_rejected"
                decision = "Reject"
                decision_reason = "Hard filters failed"
                auto_reject_reason = hard_reason
            elif ai_score < threshold:
                state = "auto_rejected"
                decision = "Reject"
                decision_reason = f"AI score below threshold ({threshold:.2f})"
                auto_reject_reason = "ai_score_too_low"
            else:
                blocked, block_reason = should_block_f2f(parsed)
                if blocked:
                    state = "auto_rejected"
                    decision = "Reject"
                    decision_reason = block_reason
                    auto_reject_reason = "f2f_non_texas"
                    draft = ""
                else:
                    routed = _analyze_email_routing(db, item["sender"], item["subject"], item["body"], item.get("snippet", ""))
                    greeting_line = greeting_from_to_contact(routed.to_email, item["body"])
                    draft = _build_user_fallback_draft(
                        db,
                        user_settings,
                        sender=item["sender"],
                        role=str(parsed["role"]),
                        parsed=parsed,
                        greeting_line=greeting_line,
                        resume_file_name=None,
                    )

            email = RecruiterEmail(
                owner_id=settings.owner_id,
                sender=item["sender"],
                subject=item["subject"],
                body=item["body"],
                role=str(parsed["role"]),
                location=str(parsed["location"]),
                salary_text=str(parsed["salary_text"]),
                skills_text=str(parsed["skills_text"]),
                score=int(ai_score * 100),
                decision=decision,
                state=state,
                decision_reason=decision_reason,
                hard_filter_result=hard_reason,
                auto_reject_reason=auto_reject_reason,
                ai_score=ai_score,
                ai_score_source=ai_score_source,
                ai_summary=ai_summary,
                semantic_embedding=email_embedding_json,
                sync_batch_id=sync_batch_id,
                draft_reply=draft,
                draft_source="rules_only" if draft else None,
                draft_model=settings.deepseek_model_fast if draft else None,
                draft_ai_error=None,
                approval_status="pending",
                sent_status="not_sent",
                source="gmail",
                external_message_id=item["external_message_id"],
                external_thread_id=item["external_thread_id"],
                external_rfc_message_id=item.get("external_rfc_message_id"),
                gmail_received_at=item.get("gmail_received_at"),
                recipient_email=item["recipient_email"],
            )
            if active_resume and resume_embedding_json and active_resume.semantic_embedding != resume_embedding_json:
                active_resume.semantic_embedding = resume_embedding_json
            db.add(email)
            imported_count += 1

        sync_run.imported_count = imported_count
        sync_run.skipped_count = skipped_count
        sync_run.error_count = error_count
        sync_run.ended_at = datetime.now(UTC)
        db.commit()
    except Exception:
        error_count += 1
        sync_run.error_count = error_count
        sync_run.ended_at = datetime.now(UTC)
        db.commit()
        raise

    last_gmail_sync_at = datetime.now(UTC)
    response = GmailSyncResponse(
        sync_batch_id=sync_batch_id,
        imported_count=imported_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )
    if telegram_service:
        telegram_service.notify(
            "Sync Digest\n"
            f"Batch: {response.sync_batch_id}\n"
            f"Imported: {response.imported_count} | Skipped: {response.skipped_count} | Errors: {response.error_count}"
        )
    return response


@app.post("/gmail/oauth/start", response_model=OAuthStartResponse)
def gmail_oauth_start() -> OAuthStartResponse:
    status, detail = start_oauth_bootstrap()
    configured, authenticated, _ = gmail_auth_status()
    return OAuthStartResponse(
        status=status,
        detail=detail,
        configured=configured,
        authenticated=authenticated,
    )


@app.post("/automation/run-once", response_model=AutomationRunResponse)
def automation_run_once(payload: AutomationRunRequest | None = None, db: Session = Depends(get_db)) -> AutomationRunResponse:
    global ai_running, ai_last_draft_source, ai_last_duration_ms, ai_last_error, ai_last_finished_at, ai_last_started_at
    if not is_gmail_configured():
        raise HTTPException(status_code=400, detail="Gmail OAuth is not configured")
    configured, authenticated, detail = gmail_auth_status()
    if configured and not authenticated:
        in_progress, last_error = oauth_bootstrap_status()
        if in_progress:
            response = AutomationRunResponse(
                status="oauth_in_progress",
                detail="OAuth is in progress. Complete sign-in from backend logs, then retry Sync + Queue.",
            )
            _record_productivity_event(
                db,
                event_type="recent_run_recorded",
                event_source="run_once",
                metadata={"status": response.status},
            )
            if telegram_service:
                telegram_service.notify(_build_telegram_digest("Run Digest", response))
            return response
        if last_error:
            response = AutomationRunResponse(
                status="oauth_required",
                detail=f"OAuth required. Trigger Connect Gmail and complete sign-in. Last OAuth error: {last_error}",
            )
            _record_productivity_event(
                db,
                event_type="recent_run_recorded",
                event_source="run_once",
                metadata={"status": response.status},
            )
            if telegram_service:
                telegram_service.notify(_build_telegram_digest("Run Digest", response))
            return response
        response = AutomationRunResponse(
            status="oauth_required",
            detail=f"{detail} Click Connect Gmail, open the auth URL from backend logs, complete sign-in, then retry.",
        )
        _record_productivity_event(
            db,
            event_type="recent_run_recorded",
            event_source="run_once",
            metadata={"status": response.status},
        )
        if telegram_service:
            telegram_service.notify(_build_telegram_digest("Run Digest", response))
        return response
    user_settings = _get_settings(db)
    resume = _active_resume(db)
    if not resume:
        raise HTTPException(status_code=400, detail="No active resume uploaded")

    requested_mail_date = payload.mail_date if payload else None
    policy = _read_policy_from_settings(user_settings)
    resolved = _resolve_effective_run_inputs(user_settings, policy, requested_mail_date)
    effective_policy = cast(PolicyConfig, resolved["policy"])
    effective_query = str(resolved["effective_query"])
    threshold = _policy_threshold(user_settings, policy)
    batch_limit = _policy_batch_limit(effective_policy, default_value=20)
    dry_run = _policy_dry_run(effective_policy)
    items = list_unread_candidates_by_query(effective_query, max_results_per_page=batch_limit)[:batch_limit]
    if not items:
        response = _build_run_response(
            "idle",
            f"No unread matching emails found for query: {effective_query}",
            effective_query=effective_query,
            matched_count=0,
            queued_count=0,
            skipped_count=0,
            failed_count=0,
        )
        _record_productivity_event(
            db,
            event_type="recent_run_recorded",
            event_source="run_once",
            metadata={
                "status": response.status,
                "matched_count": response.matched_count or 0,
                "queued_count": response.queued_count or 0,
                "skipped_count": response.skipped_count or 0,
                "failed_count": response.failed_count or 0,
            },
        )
        if telegram_service:
            telegram_service.notify(_build_telegram_digest("Run Digest", response))
        return response

    matched_count = len(items)
    queued_count = 0
    skipped_count = 0
    failed_count = 0
    last_email: RecruiterEmail | None = None

    capture_started = False
    if settings.semantic_embedding_latency_log_enabled:
        begin_embedding_latency_capture()
        capture_started = True

    try:
        active_resume = _active_resume(db)
        for item in items:
            existing = (
                db.query(RecruiterEmail)
                .filter(RecruiterEmail.owner_id == settings.owner_id)
                .filter(RecruiterEmail.external_message_id == item["external_message_id"])
                .first()
            )
            if existing and existing.state == "approved_sent":
                skipped_count += 1
                last_email = existing
                if not dry_run:
                    mark_message_processed(item["external_message_id"])
                continue

            parsed = parse_email(item["subject"], item["body"])
            hard_pass, hard_reason = hard_filter_check(parsed, user_settings)
            ai_score, ai_summary, ai_score_source, email_embedding_json, resume_embedding_json = _compute_blended_ai_score(
                subject=item["subject"],
                body=item["body"],
                parsed=parsed,
                user_settings=user_settings,
                email_row=existing,
                resume=active_resume,
            )
            if active_resume and resume_embedding_json and active_resume.semantic_embedding != resume_embedding_json:
                active_resume.semantic_embedding = resume_embedding_json

            blocked, block_reason = _policy_f2f_block(parsed, effective_policy)
            if not hard_pass or ai_score < threshold or blocked:
                if dry_run:
                    skipped_count += 1
                    continue
                email = existing or RecruiterEmail(
                    owner_id=settings.owner_id,
                    sender=item["sender"],
                    subject=item["subject"],
                    body=item["body"],
                    role=str(parsed["role"]),
                    location=str(parsed["location"]),
                    salary_text=str(parsed["salary_text"]),
                    skills_text=str(parsed["skills_text"]),
                    source="gmail",
                    external_message_id=item["external_message_id"],
                    external_thread_id=item["external_thread_id"],
                    external_rfc_message_id=item.get("external_rfc_message_id"),
                    gmail_received_at=item.get("gmail_received_at"),
                    recipient_email=item["recipient_email"],
                )
                email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
                email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
                email.score = int(ai_score * 100)
                email.ai_score = ai_score
                email.ai_score_source = ai_score_source
                email.ai_summary = ai_summary
                email.semantic_embedding = email_embedding_json or email.semantic_embedding
                email.hard_filter_result = hard_reason
                email.state = "processed_skipped"
                email.decision = "Reject"
                if blocked:
                    email.auto_reject_reason = "f2f_non_texas"
                    email.decision_reason = block_reason
                    email.skip_reason = "f2f_non_texas_blocked"
                else:
                    email.auto_reject_reason = hard_reason if not hard_pass else "ai_score_too_low"
                    email.decision_reason = "Not qualified for auto-reply"
                    email.skip_reason = "not_qualified"
                email.last_error = None
                email.draft_source = None
                email.draft_model = None
                email.draft_ai_error = None
                if not existing:
                    db.add(email)
                db.commit()
                db.refresh(email)
                mark_message_processed(item["external_message_id"])
                skipped_count += 1
                last_email = email
                continue

            routing = _analyze_email_routing(
                db,
                item["sender"],
                item["subject"],
                item["body"],
                item.get("snippet", ""),
            )
            if not routing.to_email or not routing.cc_email:
                if dry_run:
                    failed_count += 1
                    continue
                email = existing or RecruiterEmail(
                    owner_id=settings.owner_id,
                    sender=item["sender"],
                    subject=item["subject"],
                    body=item["body"],
                    role=str(parsed["role"]),
                    location=str(parsed["location"]),
                    salary_text=str(parsed["salary_text"]),
                    skills_text=str(parsed["skills_text"]),
                    source="gmail",
                    external_message_id=item["external_message_id"],
                    external_thread_id=item["external_thread_id"],
                    external_rfc_message_id=item.get("external_rfc_message_id"),
                    gmail_received_at=item.get("gmail_received_at"),
                    recipient_email=item["recipient_email"],
                )
                email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
                email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
                email.state = "failed"
                email.decision = "Reject"
                email.last_error = "Could not resolve recruiter To and employer CC"
                email.skip_reason = "missing_to_or_cc"
                email.decision_reason = "Recipient routing unresolved"
                _apply_routing_result(email, routing)
                email.routing_confirmed = False
                email.resume_asset_id = resume.id
                email.resume_file_name = resume.file_name
                if not existing:
                    db.add(email)
                db.commit()
                db.refresh(email)
                _record_productivity_event(
                    db,
                    event_type="failed_mapping_marked",
                    event_source="state",
                    entity_id=email.id,
                    metadata={"reason": email.skip_reason or "missing_to_or_cc"},
                )
                mark_message_processed(item["external_message_id"])
                failed_count += 1
                last_email = email
                continue

            # Manual approval gate: queue only, never auto-send from run-once.
            if dry_run:
                queued_count += 1
                continue
            greeting_line = greeting_from_to_contact(routing.to_email, item["body"])
            fallback_reply = _build_user_fallback_draft(
                db,
                user_settings,
                sender=item["sender"],
                role=str(parsed["role"]),
                parsed=parsed,
                greeting_line=greeting_line,
                resume_file_name=resume.file_name,
            )
            if user_settings.feature_ai_enabled:
                ai_running = True
                ai_last_error = None
                ai_last_started_at = datetime.now(UTC)
                ai_last_finished_at = None
                ai_last_duration_ms = None
                ai_last_draft_source = None
                try:
                    ai_reply = generate_reply_with_ai_or_fallback(
                        sender=item["sender"],
                        recruiter_to_email=routing.to_email,
                        greeting_line=greeting_line,
                        subject=item["subject"],
                        body=item["body"],
                        role=str(parsed["role"]),
                        location=str(parsed["location"]),
                        salary_text=str(parsed["salary_text"]),
                        skills_text=str(parsed["skills_text"]),
                        resume_path=resume.file_path,
                        resume_file_name=resume.file_name,
                        fallback_draft=fallback_reply,
                        model_name=settings.deepseek_model_fast,
                    )
                finally:
                    ai_last_finished_at = datetime.now(UTC)
                    ai_last_duration_ms = int((ai_last_finished_at - ai_last_started_at).total_seconds() * 1000)
                    ai_running = False
                reply = ai_reply.draft_text
                draft_source = ai_reply.source
                draft_model = ai_reply.ai_model
                draft_ai_error = ai_reply.ai_error
                ai_last_error = ai_reply.ai_error
                ai_last_draft_source = ai_reply.source
            else:
                reply = fallback_reply
                draft_source = "rules_only"
                draft_model = None
                draft_ai_error = None
                ai_last_draft_source = "rules_only"
            email = existing or RecruiterEmail(
                owner_id=settings.owner_id,
                sender=item["sender"],
                subject=item["subject"],
                body=item["body"],
                role=str(parsed["role"]),
                location=str(parsed["location"]),
                salary_text=str(parsed["salary_text"]),
                skills_text=str(parsed["skills_text"]),
                source="gmail",
                external_message_id=item["external_message_id"],
                external_thread_id=item["external_thread_id"],
                external_rfc_message_id=item.get("external_rfc_message_id"),
                gmail_received_at=item.get("gmail_received_at"),
                recipient_email=item["recipient_email"],
            )
            email.external_rfc_message_id = email.external_rfc_message_id or item.get("external_rfc_message_id")
            email.gmail_received_at = email.gmail_received_at or item.get("gmail_received_at")
            email.score = int(ai_score * 100)
            email.ai_score = ai_score
            email.ai_score_source = ai_score_source
            email.ai_summary = ai_summary
            email.semantic_embedding = email_embedding_json or email.semantic_embedding
            email.hard_filter_result = hard_reason
            email.draft_reply = reply
            email.draft_source = draft_source
            email.draft_model = draft_model
            email.draft_ai_error = draft_ai_error
            email.last_error = None
            email.state = "needs_review"
            email.decision = "Qualified"
            email.decision_reason = "Qualified and queued for manual approval"
            email.approval_status = "pending"
            email.sent_status = "not_sent"
            email.sent_at = None
            email.gmail_sent_id = None
            _apply_routing_result(email, routing)
            email.routing_confirmed = False
            email.resume_asset_id = resume.id
            email.resume_file_name = resume.file_name
            email.skip_reason = None
            if not existing:
                db.add(email)
            db.commit()
            db.refresh(email)
            _record_productivity_event(
                db,
                event_type="needs_review_marked",
                event_source="state",
                entity_id=email.id,
                metadata={"source": "automation_run"},
            )
            mark_message_processed(item["external_message_id"])
            queued_count += 1
            last_email = email

        if queued_count > 0:
            status = "ready"
            detail = f"Processed {matched_count} unread matching emails: queued={queued_count}, skipped={skipped_count}, failed={failed_count}."
        elif failed_count > 0:
            status = "failed"
            detail = f"Processed {matched_count} unread matching emails: queued=0, skipped={skipped_count}, failed={failed_count}."
        else:
            status = "skipped"
            detail = f"Processed {matched_count} unread matching emails: queued=0, skipped={skipped_count}, failed=0."
        if dry_run:
            detail = f"[Dry run] {detail} No database or Gmail label changes were made. (batch_limit={batch_limit}, threshold={threshold:.2f})"

        response = _build_run_response(
            status,
            detail,
            last_email,
            effective_query=effective_query,
            matched_count=matched_count,
            queued_count=queued_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
        )
        _record_productivity_event(
            db,
            event_type="recent_run_recorded",
            event_source="run_once",
            entity_id=response.email_id,
            metadata={
                "status": response.status,
                "matched_count": response.matched_count or 0,
                "queued_count": response.queued_count or 0,
                "skipped_count": response.skipped_count or 0,
                "failed_count": response.failed_count or 0,
            },
        )
        if telegram_service:
            telegram_service.notify(_build_telegram_digest("Run Digest", response))
        return response
    finally:
        if capture_started:
            latency_samples = end_embedding_latency_capture()
            if latency_samples:
                p50_ms = _percentile_ms(latency_samples, 50.0)
                p95_ms = _percentile_ms(latency_samples, 95.0)
                max_ms = max(latency_samples)
                provider = (settings.semantic_embedding_provider or "hash").strip().lower()
                model = settings.semantic_embedding_model or "text-embedding-3-small"
                logger.warning(
                    "embedding_latency_summary count=%s p50_ms=%.2f p95_ms=%.2f max_ms=%.2f provider=%s model=%s",
                    len(latency_samples),
                    p50_ms,
                    p95_ms,
                    max_ms,
                    provider,
                    model,
                )


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
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if _is_terminal_state(email):
        raise HTTPException(status_code=400, detail="Candidate is in terminal state")
    if email.state != "needs_review":
        raise HTTPException(status_code=400, detail="Only needs_review candidates can be approved")

    original_draft = email.draft_reply
    if payload.edited_reply:
        email.draft_reply = payload.edited_reply

    email.last_error = None
    sent_message_id = None
    if email.source == "gmail":
        if not email.external_thread_id or not email.recipient_email:
            raise HTTPException(status_code=400, detail="Missing Gmail metadata")
        if not email.cc_email:
            raise HTTPException(status_code=400, detail="CC email is required before sending")
        if not _routing_is_sendable(email):
            detail = email.routing_reason or "Recipient routing must be confirmed before sending"
            raise HTTPException(status_code=400, detail=f"Recipient routing is not safe to send: {detail}")
        if not email.draft_reply.strip():
            raise HTTPException(status_code=400, detail="Draft email body is required before sending")
        resume = _active_resume(db)
        if not resume:
            raise HTTPException(status_code=400, detail="No active resume uploaded")
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name
        try:
            sent_message_id = send_reply_with_attachment(
                email.external_thread_id,
                email.recipient_email,
                email.cc_email,
                email.subject,
                email.draft_reply,
                resume.file_path,
            )
            if email.external_message_id:
                mark_message_processed(email.external_message_id)
        except Exception as exc:
            email.last_error = str(exc)
            db.commit()
            db.refresh(email)
            raise HTTPException(status_code=502, detail=f"Gmail send failed: {exc}") from exc

    email.state = "approved_sent"
    email.decision = "Qualified"
    email.approval_status = "approved"
    email.sent_status = "sent"
    email.sent_at = datetime.now(UTC)
    email.gmail_sent_id = sent_message_id
    if payload.edited_reply and payload.edited_reply.strip() != original_draft.strip():
        db.add(
            DraftEditFeedback(
                owner_id=settings.owner_id,
                recruiter_email_id=email.id,
                original_draft=original_draft,
                edited_draft=payload.edited_reply,
            )
        )
    db.commit()
    db.refresh(email)
    _record_productivity_event(
        db,
        event_type="approved_sent",
        event_source="action",
        entity_id=email.id,
        metadata={"state": email.state, "sent_status": email.sent_status},
    )

    try:
        logger.info("Appending Google Sheets tracking row for approved email_id=%s", email.id)
        append_tracking_sheet_row(
            role=email.role,
            sender=email.sender,
            subject=email.subject,
            body=email.body,
            to_email=email.recipient_email,
            cc_email=email.cc_email,
        )
        logger.info("Google Sheets tracking row appended for email_id=%s", email.id)
    except Exception as exc:
        warning = f"Sheets tracking warning: {exc}"
        logger.warning("Failed to append Google Sheets tracking row for email_id=%s: %s", email.id, exc)
        email.last_error = warning[:2000]
        db.commit()
        db.refresh(email)

    return email


@app.post("/candidates/{email_id}/reject", response_model=EmailResponse)
def reject_candidate(
    email_id: int,
    payload: RejectRequest,
    db: Session = Depends(get_db),
) -> RecruiterEmail:
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")
    if _is_terminal_state(email):
        raise HTTPException(status_code=400, detail="Candidate is in terminal state")
    if email.state != "needs_review":
        raise HTTPException(status_code=400, detail="Only needs_review candidates can be rejected")

    email.state = "rejected"
    email.decision = "Reject"
    email.decision_reason = payload.reason or "Rejected by user"
    email.approval_status = "rejected"
    email.sent_status = "not_sent"
    db.commit()
    db.refresh(email)
    return email


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
    email = (
        db.query(RecruiterEmail)
        .filter(RecruiterEmail.owner_id == settings.owner_id, RecruiterEmail.id == email_id)
        .first()
    )
    if not email:
        raise HTTPException(status_code=404, detail="Candidate not found")

    to_email = payload.to_email.strip()
    cc_email = payload.cc_email.strip()
    email.recipient_email = to_email
    email.cc_email = cc_email
    email.routing_status = "confirmed"
    email.routing_confidence = 1.0
    email.routing_reason = "Recipient routing manually confirmed."
    email.routing_evidence = json.dumps(
        [
            {
                "role": "to",
                "email": to_email,
                "source": "manual_edit",
                "detail": "Confirmed by user",
            },
            {
                "role": "cc",
                "email": cc_email,
                "source": "manual_edit",
                "detail": "Confirmed by user",
            },
        ]
    )
    email.routing_candidates = email.routing_evidence
    email.routing_confirmed = True
    email.state = "needs_review"
    email.last_error = None
    email.skip_reason = None
    email.decision_reason = "Recipient routing corrected by user"

    parsed = parse_email(email.subject, email.body)
    role = str(parsed["role"])
    greeting_line = greeting_from_to_contact(to_email, email.body)
    user_settings = _get_settings(db)
    resume = _active_resume(db)
    fallback_reply = _build_user_fallback_draft(
        db,
        user_settings,
        sender=email.sender,
        role=role,
        parsed=parsed,
        greeting_line=greeting_line,
        resume_file_name=resume.file_name if resume else email.resume_file_name,
    )
    reply = fallback_reply
    draft_source = "rules_only"
    draft_model = None
    draft_ai_error = None
    if resume:
        email.resume_asset_id = resume.id
        email.resume_file_name = resume.file_name

    if user_settings.feature_ai_enabled:
        if resume:
            ai_reply = generate_reply_with_ai_or_fallback(
                sender=email.sender,
                recruiter_to_email=to_email,
                greeting_line=greeting_line,
                subject=email.subject,
                body=email.body,
                role=role,
                location=str(parsed["location"]),
                salary_text=str(parsed["salary_text"]),
                skills_text=str(parsed["skills_text"]),
                resume_path=resume.file_path,
                resume_file_name=resume.file_name,
                fallback_draft=fallback_reply,
                model_name=settings.deepseek_model_fast,
            )
            reply = ai_reply.draft_text
            draft_source = ai_reply.source
            draft_model = ai_reply.ai_model
            draft_ai_error = ai_reply.ai_error
        else:
            draft_ai_error = "AI enabled but no active resume uploaded; generated rules-only fallback draft."

    email.role = role
    email.location = str(parsed["location"])
    email.salary_text = str(parsed["salary_text"])
    email.skills_text = str(parsed["skills_text"])
    email.draft_reply = reply
    email.draft_source = draft_source
    email.draft_model = draft_model
    email.draft_ai_error = draft_ai_error

    sender_domain = _email_domain(email.sender)
    body_lower = (email.body or "").lower()
    if sender_domain:
        db.add(
            RecipientRoutingFeedback(
                owner_id=settings.owner_id,
                sender_domain=sender_domain,
                corrected_to=to_email,
                corrected_cc=cc_email,
                sample_sender=email.sender,
                evidence_to_present=to_email.lower() in body_lower,
                evidence_cc_present=cc_email.lower() in body_lower or cc_email.lower() in email.sender.lower(),
                sample_body=email.body[:5000] if email.body else None,
            )
        )

    db.commit()
    db.refresh(email)
    _record_productivity_event(
        db,
        event_type="needs_review_marked",
        event_source="state",
        entity_id=email.id,
        metadata={"source": "resolve_recipients"},
    )
    return email
