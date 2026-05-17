from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import logging
import threading
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import RecruiterEmail, SyncRun, UserSettings
from app.schemas import ApproveSendRequest, AutomationRunResponse, RejectRequest
from app.services.telegram_runtime import TelegramRuntimeState
from app.telegram_bot import TelegramReply

logger = logging.getLogger(__name__)

TELEGRAM_MENU_PAGE_SIZE = 6


@dataclass
class TelegramRuntimeDeps:
    session_factory: Callable[[], Session]
    get_settings: Callable[[Session], UserSettings]
    read_policy_from_settings: Callable[[UserSettings], Any]
    policy_dry_run: Callable[[Any], bool]
    format_query_preflight: Callable[[UserSettings, Any], str]
    poll_interval_minutes: Callable[[UserSettings], int]
    build_telegram_digest: Callable[[str, AutomationRunResponse], str]
    gmail_auth_status: Callable[[], tuple[bool, bool, str]]
    ai_status: Callable[[], Any]
    gmail_sync: Callable[[Session], Any]
    automation_run_once: Callable[[Any, Session], AutomationRunResponse]
    approve_and_send: Callable[[int, ApproveSendRequest, Session], Any]
    reject_candidate: Callable[[int, RejectRequest, Session], Any]
    owner_id: str
    action_lock: threading.Lock
    action_pin: Callable[[], str]
    auth_ttl_minutes: Callable[[], int]


class TelegramRuntime:
    def __init__(self, deps: TelegramRuntimeDeps):
        self.deps = deps

    @staticmethod
    def parse_allowed_chat_ids(raw: str) -> set[int]:
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

    @staticmethod
    def _is_valid_iso_date(value: str) -> bool:
        try:
            date.fromisoformat(value)
            return True
        except ValueError:
            return False

    @staticmethod
    def _extract_pin(parts: list[str]) -> tuple[list[str], str | None]:
        clean: list[str] = []
        pin: str | None = None
        for part in parts:
            if part.lower().startswith("pin="):
                pin = part.split("=", 1)[1].strip()
                continue
            clean.append(part)
        return clean, pin

    @staticmethod
    def _tg_btn(text: str, data: str) -> dict[str, str]:
        return {"text": text, "callback_data": data}

    @classmethod
    def _parse_callback_data(cls, data: str) -> tuple[str, int]:
        parts = data.split(":", 2)
        action = parts[1] if len(parts) >= 2 else ""
        page = 0
        if len(parts) >= 3:
            try:
                page = max(0, int(parts[2]))
            except ValueError:
                page = 0
        return action, page

    @classmethod
    def _paginate_buttons(
        cls,
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
            nav_row.append(cls._tg_btn("Back", f"menu:{menu_action}:{(start // TELEGRAM_MENU_PAGE_SIZE) - 1}"))
        if end < total:
            nav_row.append(cls._tg_btn("More", f"menu:{menu_action}:{(start // TELEGRAM_MENU_PAGE_SIZE) + 1}"))
        if nav_row:
            rows.append(nav_row)

        foot_row: list[dict[str, str]] = []
        if include_back:
            foot_row.append(cls._tg_btn("Sections", "menu:main:0"))
        if include_home:
            foot_row.append(cls._tg_btn("Home", "menu:main:0"))
        if foot_row:
            rows.append(foot_row)
        return rows

    @classmethod
    def _main_menu_reply(cls) -> TelegramReply:
        buttons = [
            cls._tg_btn("Read-only", "menu:readonly:0"),
            cls._tg_btn("Config", "menu:config:0"),
            cls._tg_btn("Actions", "menu:actions:0"),
            cls._tg_btn("Profile/Auth", "menu:profile:0"),
        ]
        return TelegramReply(
            text="MailOps bot is active. Choose a section:",
            inline_keyboard=[[button] for button in buttons],
        )

    @classmethod
    def _menu_reply(cls, action: str, page: int = 0) -> TelegramReply:
        title = "Menu"
        buttons: list[dict[str, str]] = []
        if action == "readonly":
            title = "Read-only"
            buttons = [
                cls._tg_btn("Status", "cmd:/status"),
                cls._tg_btn("Needs Review", "cmd:/needs_review"),
                cls._tg_btn("Failed Mapping", "cmd:/failed_mapping"),
                cls._tg_btn("Recent Runs", "cmd:/recent_runs"),
            ]
        elif action == "config":
            title = "Config"
            buttons = [
                cls._tg_btn("Set Query", "flow:await_setquery"),
                cls._tg_btn("Set Date", "flow:await_setdate"),
                cls._tg_btn("Set Default Query", "flow:await_setdefaultquery"),
                cls._tg_btn("Set Default Date", "flow:await_setdefaultdate"),
                cls._tg_btn("Auto Run ON", "cmd:/setautorun on"),
                cls._tg_btn("Auto Run OFF", "cmd:/setautorun off"),
                cls._tg_btn("Set Auto Interval", "flow:await_setautointerval"),
            ]
        elif action == "actions":
            title = "Actions"
            buttons = [
                cls._tg_btn("Run", "cmd:/run"),
                cls._tg_btn("Sync", "cmd:/sync"),
                cls._tg_btn("Approve by ID", "flow:await_approve_id"),
                cls._tg_btn("Reject by ID", "flow:await_reject_id"),
            ]
        elif action == "profile":
            title = "Profile/Auth"
            buttons = [
                cls._tg_btn("Profile", "cmd:/profile"),
                cls._tg_btn("Authenticate", "flow:await_auth_pin"),
                cls._tg_btn("Logout", "cmd:/logout"),
                cls._tg_btn("Main Menu", "menu:main:0"),
            ]
        else:
            return cls._main_menu_reply()

        return TelegramReply(
            text=f"{title} menu:",
            inline_keyboard=cls._paginate_buttons(buttons, page, menu_action=action, include_back=True),
        )

    @staticmethod
    def _pending_prompt(mode: str) -> str:
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

    @staticmethod
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

    def _action_authorized(self, pin: str | None) -> bool:
        configured_pin = (self.deps.action_pin() or "").strip()
        if not configured_pin:
            return True
        return bool(pin and pin == configured_pin)

    def _session_remaining(self, chat_id: int) -> str:
        return TelegramRuntimeState.session_remaining(chat_id)

    def _require_action_auth(self, chat_id: int, cmd: str, pin: str | None) -> str | None:
        if TelegramRuntimeState.session_is_active(chat_id):
            return None
        if self._action_authorized(pin):
            return None
        logger.info("Telegram auth denied chat_id=%s cmd=%s reason=missing_or_invalid_auth", chat_id, cmd)
        return "Action blocked. Run /auth <PIN> or provide pin=<PIN>."

    def handle_command(self, chat_id: int, user_id: str, username: str, text: str) -> str | TelegramReply:
        _ = user_id
        command_line = text.strip()
        if not command_line:
            return "Empty command."
        if command_line.lower() == "/menu":
            return self._main_menu_reply()

        pending_mode = TelegramRuntimeState.get_pending_mode(chat_id)
        if pending_mode and not command_line.startswith("/"):
            TelegramRuntimeState.clear_pending_mode(chat_id)
            if pending_mode == "await_setquery":
                return self.handle_command(chat_id, user_id, username, f"/setquery {command_line}")
            if pending_mode == "await_setdate":
                return self.handle_command(chat_id, user_id, username, f"/setdate {command_line}")
            if pending_mode == "await_setdefaultquery":
                return self.handle_command(chat_id, user_id, username, f"/setdefaultquery {command_line}")
            if pending_mode == "await_setdefaultdate":
                return self.handle_command(chat_id, user_id, username, f"/setdefaultdate {command_line}")
            if pending_mode == "await_setautointerval":
                return self.handle_command(chat_id, user_id, username, f"/setautointerval {command_line}")
            if pending_mode == "await_approve_id":
                return self.handle_command(chat_id, user_id, username, f"/approve {command_line}")
            if pending_mode == "await_reject_id":
                return self.handle_command(chat_id, user_id, username, f"/reject {command_line} Rejected from Telegram")
            if pending_mode == "await_auth_pin":
                return self.handle_command(chat_id, user_id, username, f"/auth {command_line}")

        parts = command_line.split()
        cmd = parts[0].lower()
        args, pin = self._extract_pin(parts[1:])
        logger.info("Telegram command received chat_id=%s user=%s cmd=%s", chat_id, username, cmd)

        if cmd == "/start":
            return self._main_menu_reply()

        db = self.deps.session_factory()
        try:
            if cmd == "/auth":
                if not args:
                    return "Usage: /auth <PIN>"
                configured_pin = (self.deps.action_pin() or "").strip()
                if not configured_pin:
                    TelegramRuntimeState.activate_session(chat_id, self.deps.auth_ttl_minutes())
                    logger.info("Telegram auth success chat_id=%s cmd=%s mode=no_configured_pin", chat_id, cmd)
                    return f"Authenticated. Session expires in {self._session_remaining(chat_id)}."
                supplied_pin = args[0].strip()
                if supplied_pin != configured_pin:
                    logger.info("Telegram auth failed chat_id=%s cmd=%s reason=wrong_pin", chat_id, cmd)
                    return "Authentication failed: incorrect PIN."
                TelegramRuntimeState.activate_session(chat_id, self.deps.auth_ttl_minutes())
                logger.info("Telegram auth success chat_id=%s cmd=%s", chat_id, cmd)
                return f"Authenticated. Session expires in {self._session_remaining(chat_id)}."

            if cmd == "/logout":
                TelegramRuntimeState.clear_session(chat_id)
                logger.info("Telegram logout chat_id=%s cmd=%s", chat_id, cmd)
                return "Logged out. Action commands now require /auth <PIN> or pin=<PIN>."

            if cmd == "/status":
                gmail_configured, gmail_authenticated, gmail_detail = self.deps.gmail_auth_status()
                ai_info = self.deps.ai_status()
                user_settings = self.deps.get_settings(db)
                policy = self.deps.read_policy_from_settings(user_settings)
                dry_run = self.deps.policy_dry_run(policy)
                is_authenticated = TelegramRuntimeState.session_is_active(chat_id)
                auth_line = f"Authenticated: {'yes' if is_authenticated else 'no'}"
                if is_authenticated:
                    auth_line += f" (expires in {self._session_remaining(chat_id)})"
                return (
                    f"Gmail: {'Authenticated' if gmail_authenticated else 'Not authenticated'} (configured={gmail_configured})\n"
                    f"AI: {'Healthy' if ai_info.connected else 'Disconnected'} ({ai_info.model})\n"
                    f"{auth_line}\n"
                    f"Dry run: {dry_run}\n"
                    f"Auto run: {'on' if user_settings.feature_auto_polling else 'off'} ({self.deps.poll_interval_minutes(user_settings)} min)\n"
                    "Source: /run uses saved backend settings below.\n"
                    f"Query: {user_settings.gmail_query}\n"
                    f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
                    f"Date: {user_settings.mail_date or 'any'}\n"
                    f"Default date mode: {(user_settings.default_date_mode or 'today').strip().lower() if (user_settings.default_date_mode or '').strip().lower() in {'today', 'off'} else 'today'}\n"
                    f"Detail: {gmail_detail}\n"
                    "Hint: Use /setquery and /setdate to change what /run searches."
                )

            if cmd == "/profile":
                user_settings = self.deps.get_settings(db)
                mode = (user_settings.default_date_mode or "").strip().lower()
                if mode not in {"today", "off"}:
                    mode = "today"
                return (
                    "Profile defaults:\n"
                    f"Default query: {user_settings.default_gmail_query or user_settings.gmail_query}\n"
                    f"Default date mode: {mode}\n"
                    f"Auto run: {'on' if user_settings.feature_auto_polling else 'off'} ({self.deps.poll_interval_minutes(user_settings)} min)\n"
                    f"Active query: {user_settings.gmail_query}\n"
                    f"Active date: {user_settings.mail_date or 'any'}"
                )

            if cmd == "/setquery":
                query_text = " ".join(args).strip()
                if not query_text:
                    return "Usage: /setquery <gmail query>"
                user_settings = self.deps.get_settings(db)
                user_settings.gmail_query = query_text
                db.commit()
                logger.info("Telegram config update chat_id=%s field=gmail_query", chat_id)
                return f"Query updated to: {user_settings.gmail_query}"

            if cmd == "/setdefaultquery":
                query_text = " ".join(args).strip()
                if not query_text:
                    return "Usage: /setdefaultquery <gmail query>"
                user_settings = self.deps.get_settings(db)
                user_settings.default_gmail_query = query_text
                db.commit()
                logger.info("Telegram config update chat_id=%s field=default_gmail_query", chat_id)
                return f"Default query updated to: {user_settings.default_gmail_query}"

            if cmd == "/setdate":
                if not args:
                    return "Usage: /setdate YYYY-MM-DD or /setdate any"
                raw_value = args[0].strip().lower()
                user_settings = self.deps.get_settings(db)
                if raw_value in {"any", "clear", "none"}:
                    user_settings.mail_date = None
                    db.commit()
                    logger.info("Telegram config update chat_id=%s field=mail_date value=any", chat_id)
                    return "Mail date filter cleared. Runs will use any date."
                if not self._is_valid_iso_date(raw_value):
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
                user_settings = self.deps.get_settings(db)
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
                user_settings = self.deps.get_settings(db)
                user_settings.feature_auto_polling = mode == "on"
                db.commit()
                logger.info("Telegram config update chat_id=%s field=feature_auto_polling value=%s", chat_id, mode)
                return f"Auto run set to: {mode} (interval={self.deps.poll_interval_minutes(user_settings)} min)"

            if cmd == "/setautointerval":
                if not args:
                    return "Usage: /setautointerval <minutes>"
                try:
                    minutes = int(args[0])
                except ValueError:
                    return "Invalid interval. Use /setautointerval <minutes>."
                minutes = max(1, min(minutes, 1440))
                user_settings = self.deps.get_settings(db)
                user_settings.feature_auto_poll_interval_minutes = minutes
                db.commit()
                logger.info("Telegram config update chat_id=%s field=feature_auto_poll_interval_minutes value=%s", chat_id, minutes)
                return f"Auto run interval set to: {minutes} minute(s)."

            if cmd == "/needs_review":
                rows = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.state == "needs_review")
                    .order_by(RecruiterEmail.created_at.desc())
                    .limit(5)
                    .all()
                )
                count = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.state == "needs_review")
                    .count()
                )
                return f"Needs Review: {count}\nTop items:\n{self._format_candidate_lines(rows)}"

            if cmd == "/failed_mapping":
                rows = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.state == "failed")
                    .order_by(RecruiterEmail.created_at.desc())
                    .limit(5)
                    .all()
                )
                count = (
                    db.query(RecruiterEmail)
                    .filter(RecruiterEmail.owner_id == self.deps.owner_id, RecruiterEmail.state == "failed")
                    .count()
                )
                return f"Failed Mapping: {count}\nTop items:\n{self._format_candidate_lines(rows)}"

            if cmd == "/recent_runs":
                runs = (
                    db.query(SyncRun)
                    .filter(SyncRun.owner_id == self.deps.owner_id)
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
                auth_error = self._require_action_auth(chat_id, cmd, pin)
                if auth_error:
                    return auth_error
                with self.deps.action_lock:
                    result = self.deps.gmail_sync(db)
                return (
                    "Advanced sync finished (import-only, no queue/send).\n"
                    f"Batch: {result.sync_batch_id}\n"
                    f"Imported: {result.imported_count} | Skipped: {result.skipped_count} | Errors: {result.error_count}"
                )

            if cmd == "/run":
                auth_error = self._require_action_auth(chat_id, cmd, pin)
                if auth_error:
                    return auth_error
                user_settings = self.deps.get_settings(db)
                policy = self.deps.read_policy_from_settings(user_settings)
                preflight = self.deps.format_query_preflight(user_settings, policy)
                with self.deps.action_lock:
                    run_result = self.deps.automation_run_once(None, db)
                if run_result.status == "idle":
                    return (
                        f"{preflight}\n\n"
                        f"{self.deps.build_telegram_digest('Run finished.', run_result)}\n\n"
                        "Guidance: No matches for saved query/date.\n"
                        "Try: /setquery <gmail query>\n"
                        "Try: /setdate YYYY-MM-DD or /setdate any"
                    )
                return f"{preflight}\n\n{self.deps.build_telegram_digest('Run finished.', run_result)}"

            if cmd == "/approve":
                auth_error = self._require_action_auth(chat_id, cmd, pin)
                if auth_error:
                    return auth_error
                if not args:
                    return "Usage: /approve <email_id>"
                try:
                    email_id = int(args[0])
                except ValueError:
                    return "Invalid email_id. Usage: /approve <email_id>"
                with self.deps.action_lock:
                    email = self.deps.approve_and_send(email_id, ApproveSendRequest(edited_reply=None), db)
                return f"Approved and sent: #{email.id} | {email.subject}"

            if cmd == "/reject":
                auth_error = self._require_action_auth(chat_id, cmd, pin)
                if auth_error:
                    return auth_error
                if not args:
                    return "Usage: /reject <email_id> [reason...]"
                try:
                    email_id = int(args[0])
                except ValueError:
                    return "Invalid email_id. Usage: /reject <email_id> [reason...]"
                reason = " ".join(args[1:]).strip() or "Rejected from Telegram"
                with self.deps.action_lock:
                    email = self.deps.reject_candidate(email_id, RejectRequest(reason=reason), db)
                return f"Rejected: #{email.id} | reason={email.decision_reason or reason}"

            reply = TelegramReply(
                text="Unknown command. Use Menu below (typed /commands still work).",
                inline_keyboard=[[self._tg_btn("Open Menu", "menu:main:0")]],
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

    def handle_callback(
        self,
        chat_id: int,
        user_id: str,
        username: str,
        callback_data: str,
        message_id: int,
    ) -> str | TelegramReply:
        _ = user_id
        _ = username
        action, page = self._parse_callback_data(callback_data)

        if callback_data.startswith("menu:"):
            reply = self._menu_reply(action, page)
            reply.edit_message_id = message_id
            reply.callback_notice = "Updated."
            return reply

        if callback_data == "cancel:pending":
            TelegramRuntimeState.clear_pending_mode(chat_id)
            reply = self._main_menu_reply()
            reply.edit_message_id = message_id
            reply.callback_notice = "Canceled."
            return reply

        if callback_data.startswith("flow:"):
            mode = callback_data.split(":", 1)[1].strip()
            TelegramRuntimeState.set_pending_mode(chat_id, mode)
            return TelegramReply(
                text=self._pending_prompt(mode),
                inline_keyboard=[[self._tg_btn("Cancel", "cancel:pending")], [self._tg_btn("Home", "menu:main:0")]],
                edit_message_id=message_id,
                callback_notice="Awaiting input.",
            )

        if callback_data.startswith("cmd:"):
            command_text = callback_data.split(":", 1)[1].strip()
            result = self.handle_command(chat_id, user_id, username, command_text)
            if isinstance(result, TelegramReply):
                if result.edit_message_id is None:
                    result.edit_message_id = message_id
                if result.callback_notice is None:
                    result.callback_notice = "Done."
                return result
            return TelegramReply(
                text=result,
                inline_keyboard=[[self._tg_btn("Back", "menu:main:0")]],
                edit_message_id=message_id,
                callback_notice="Done.",
            )

        return TelegramReply(
            text="Unknown action. Opening main menu.",
            inline_keyboard=self._main_menu_reply().inline_keyboard,
            edit_message_id=message_id,
            callback_notice="Unknown action.",
        )
