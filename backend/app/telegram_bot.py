import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

import httpx

from app.services.telegram_format import chunk, escape, plain_text

logger = logging.getLogger(__name__)


@dataclass
class TelegramReply:
    text: str
    inline_keyboard: list[list[dict[str, str]]] | None = None
    edit_message_id: int | None = None
    callback_notice: str | None = None
    html: bool = False
    enqueue_chat_text: str | None = None


CommandHandler = Callable[[int, str, str, str], str | TelegramReply]
CallbackHandler = Callable[[int, str, str, str, int], str | TelegramReply]
ChatTurnHandler = Callable[[int, int, str], None]


@dataclass
class TelegramBotStatus:
    enabled: bool
    polling: bool
    alerts_enabled: bool
    authorized_chats: int
    detail: str


class TelegramAPIError(RuntimeError):
    def __init__(self, error_code: int, description: str, retry_after: float | None = None) -> None:
        super().__init__(f"Telegram API error {error_code}: {description}")
        self.error_code = error_code
        self.description = description
        self.retry_after = retry_after


class TelegramTransport:
    def __init__(self, token: str) -> None:
        self._token = token.strip()
        self._edit_lock = threading.Lock()
        self._last_edit_at: dict[int, float] = {}

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _request_json(self, method: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url(method),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=35) as resp:
                body = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            parameters = parsed.get("parameters") if isinstance(parsed.get("parameters"), dict) else {}
            retry_after = parameters.get("retry_after")
            raise TelegramAPIError(
                int(parsed.get("error_code", 0)),
                str(parsed.get("description", "request failed")),
                float(retry_after) if retry_after is not None else None,
            )
        return parsed

    def _post_json(self, method: str, payload: dict) -> dict:
        try:
            return self._request_json(method, payload)
        except TelegramAPIError as exc:
            if exc.error_code != 429 or exc.retry_after is None:
                raise
            time.sleep(exc.retry_after)
            return self._request_json(method, payload)

    def _formatted_post(self, method: str, payload: dict, text: str) -> dict:
        try:
            return self._post_json(method, payload)
        except TelegramAPIError as exc:
            if exc.error_code != 400 or "parse" not in exc.description.lower():
                raise
            logger.warning("Telegram formatting rejected method=%s code=%s", method, exc.error_code)
            fallback = dict(payload)
            fallback.pop("parse_mode", None)
            fallback["text"] = plain_text(text)
            return self._post_json(method, fallback)

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> int | None:
        message_id: int | None = None
        parts = chunk(text) or [""]
        for index, part in enumerate(parts):
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if inline_keyboard and index == len(parts) - 1:
                payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
            result = self._formatted_post("sendMessage", payload, part).get("result")
            if message_id is None and isinstance(result, dict) and result.get("message_id") is not None:
                message_id = int(result["message_id"])
        return message_id

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> None:
        parts = chunk(text) or [""]
        with self._edit_lock:
            wait = 2.0 - (time.monotonic() - self._last_edit_at.get(chat_id, 0.0))
            if wait > 0:
                time.sleep(wait)
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": parts[0],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if inline_keyboard and len(parts) == 1:
                payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
            self._formatted_post("editMessageText", payload, parts[0])
            self._last_edit_at[chat_id] = time.monotonic()
        for index, part in enumerate(parts[1:], start=1):
            self.send_message(
                chat_id,
                part,
                inline_keyboard=inline_keyboard if index == len(parts) - 1 else None,
            )

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:180]
        self._post_json("answerCallbackQuery", payload)

    def send_photo(self, chat_id: int, photo: bytes, *, caption: str | None = None) -> None:
        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data.update({"caption": caption, "parse_mode": "HTML"})
        response = httpx.post(
            self._api_url("sendPhoto"),
            data=data,
            files={"photo": ("chart.png", photo, "image/png")},
            timeout=35,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise TelegramAPIError(response.status_code, "invalid response") from exc
        if response.status_code >= 400 or not payload.get("ok"):
            raise TelegramAPIError(
                int(payload.get("error_code", response.status_code)),
                str(payload.get("description", "request failed")),
            )


class TelegramBotService:
    def __init__(
        self,
        *,
        token: str,
        alerts_enabled: bool,
        is_authorized: Callable[[int, str], bool],
        chat_ids_for_owner: Callable[[str], list[int]],
        authorized_chat_count: Callable[[], int],
        command_handler: CommandHandler,
        callback_handler: CallbackHandler,
        chat_turn_handler: ChatTurnHandler | None = None,
        # Whether this process should be the one polling. Injected and
        # defaulted so existing construction sites and tests keep working
        # without Redis.
        is_leader: Callable[[], bool] = lambda: True,
    ) -> None:
        self._token = token.strip()
        self.transport = TelegramTransport(self._token)
        self._alerts_enabled = alerts_enabled
        self._is_authorized = is_authorized
        self._chat_ids_for_owner = chat_ids_for_owner
        self._authorized_chat_count = authorized_chat_count
        self._command_handler = command_handler
        self._callback_handler = callback_handler
        self._chat_turn_handler = chat_turn_handler
        self._is_leader = is_leader
        self._offset = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._recent_actions: dict[str, float] = {}
        self._dedupe_ttl_seconds = 15.0
        self._polling = False
        self._detail = "Telegram bot is not running"
        self._bot_username = ""

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    @property
    def bot_username(self) -> str:
        return self._bot_username

    def status(self) -> TelegramBotStatus:
        with self._lock:
            return TelegramBotStatus(
                enabled=self.enabled,
                polling=self._polling,
                alerts_enabled=self._alerts_enabled,
                authorized_chats=self._authorized_chat_count(),
                detail=self._detail,
            )

    def start(self) -> None:
        if not self.enabled:
            with self._lock:
                self._detail = "Telegram bot disabled: TELEGRAM_BOT_TOKEN is missing"
            return
        if self._thread and self._thread.is_alive():
            return
        try:
            result = self.transport._post_json("getMe", {}).get("result", {})
            self._bot_username = str(result.get("username", "")).strip() if isinstance(result, dict) else ""
        except Exception:
            self._bot_username = ""
            logger.warning("Telegram bot identity lookup failed", exc_info=True)
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, name="telegram-bot-poller", daemon=True)
        self._thread.start()
        with self._lock:
            self._detail = "Telegram bot started"

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        with self._lock:
            self._polling = False
            self._detail = "Telegram bot stopped"

    def notify_owner(self, owner_id: str, text: str) -> None:
        if not self._alerts_enabled:
            return
        safe_text = text.strip()
        if not safe_text:
            return
        for chat_id in self._chat_ids_for_owner(owner_id):
            self._send_message(chat_id, safe_text)

    def _post_json(self, method: str, payload: dict) -> dict:
        return self.transport._post_json(method, payload)

    def _send_message(
        self,
        chat_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        html: bool = False,
    ) -> int | None:
        try:
            return self.transport.send_message(chat_id, text if html else escape(text), inline_keyboard=inline_keyboard)
        except Exception as exc:
            logger.warning("Failed to send Telegram message to chat_id=%s: %s", chat_id, exc)
            return None

    def _edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
        html: bool = False,
    ) -> None:
        try:
            self.transport.edit_message(
                chat_id,
                message_id,
                text if html else escape(text),
                inline_keyboard=inline_keyboard,
            )
        except Exception as exc:
            logger.warning(
                "Failed to edit Telegram message chat_id=%s message_id=%s: %s",
                chat_id,
                message_id,
                exc,
            )

    def _answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            self.transport.answer_callback_query(callback_query_id, text)
        except Exception as exc:
            logger.warning("Failed to answer callback query id=%s: %s", callback_query_id, exc)

    def _poll_updates(self) -> list[dict]:
        payload = {
            "timeout": 30,
            "allowed_updates": ["message", "callback_query"],
        }
        if self._offset > 0:
            payload["offset"] = self._offset
        response = self._post_json("getUpdates", payload)
        updates = response.get("result", [])
        if not isinstance(updates, list):
            return []
        return [item for item in updates if isinstance(item, dict)]

    def _is_duplicate_action(self, action_key: str) -> bool:
        now = time.time()
        expired = [k for k, ts in self._recent_actions.items() if now - ts > self._dedupe_ttl_seconds]
        for key in expired:
            self._recent_actions.pop(key, None)
        if action_key in self._recent_actions:
            return True
        self._recent_actions[action_key] = now
        return False

    def _run_loop(self) -> None:
        standing_by = False
        while self._running:
            # One poller per deployment. Telegram answers a second concurrent
            # getUpdates on the same token with 409 Conflict, and splits
            # updates unpredictably between the two - so a user's multi-step
            # flow would half-work depending on which process received which
            # message.
            if not self._is_leader():
                if not standing_by:
                    logger.info("Telegram poller standing by; another process holds the lease")
                    standing_by = True
                with self._lock:
                    self._polling = False
                    self._detail = "Telegram bot standing by (another process is polling)"
                time.sleep(5)
                continue
            if standing_by:
                logger.info("Telegram poller taking over")
                standing_by = False
            try:
                with self._lock:
                    self._polling = True
                    self._detail = "Telegram bot polling"
                updates = self._poll_updates()
                for update in updates:
                    update_id = int(update.get("update_id", 0))
                    if update_id >= self._offset:
                        self._offset = update_id + 1
                    self._handle_update(update)
            except error.URLError as exc:
                with self._lock:
                    self._detail = f"Telegram network error: {exc}"
                time.sleep(2)
            except Exception as exc:
                with self._lock:
                    self._detail = f"Telegram polling error: {exc}"
                logger.exception("Telegram polling loop error")
                time.sleep(2)

        with self._lock:
            self._polling = False

    def _handle_update(self, update: dict) -> None:
        callback_query = update.get("callback_query")
        if isinstance(callback_query, dict):
            self._handle_callback_query(callback_query)
            return

        message = update.get("message")
        if not isinstance(message, dict):
            return
        text = str(message.get("text", "")).strip()
        if not text:
            return
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        chat_id = int(chat.get("id", 0))
        user_id = str(from_user.get("id", "unknown"))
        username = str(from_user.get("username", "")).strip() or user_id

        parts = text.split(maxsplit=1)
        is_link_start = len(parts) == 2 and parts[0].lower() == "/start"
        if is_link_start and str(chat.get("type", "")) != "private":
            self._send_message(chat_id, "Only private Telegram chats can be linked.")
            return
        if not self._is_authorized(chat_id, text):
            self._send_message(chat_id, "Unauthorized chat. Access denied.")
            return

        if text.startswith("/"):
            action_key = f"{chat_id}:{text.lower()}"
            if self._is_duplicate_action(action_key):
                self._send_message(chat_id, "Duplicate command ignored (tap detected twice).")
                return

        try:
            reply = self._command_handler(chat_id, user_id, username, text)
        except Exception as exc:
            logger.exception("Telegram command handler failure")
            reply = f"Command failed: {exc}"
        sent_message_id = self._send_reply(chat_id, reply)
        if (
            isinstance(reply, TelegramReply)
            and reply.enqueue_chat_text is not None
            and sent_message_id is not None
            and self._chat_turn_handler is not None
        ):
            try:
                self._chat_turn_handler(chat_id, sent_message_id, reply.enqueue_chat_text)
            except Exception as exc:
                logger.warning(
                    "Failed to enqueue Telegram chat turn chat_id=%s error_type=%s",
                    chat_id,
                    type(exc).__name__,
                )
                self._edit_message(chat_id, sent_message_id, "Something went wrong on my side.")

    def _handle_callback_query(self, callback_query: dict) -> None:
        callback_query_id = str(callback_query.get("id", "")).strip()
        data = str(callback_query.get("data", "")).strip()
        if not callback_query_id or not data:
            return

        message = callback_query.get("message", {})
        if not isinstance(message, dict):
            return
        chat = message.get("chat", {})
        from_user = callback_query.get("from", {})
        chat_id = int(chat.get("id", 0))
        message_id = int(message.get("message_id", 0))
        user_id = str(from_user.get("id", "unknown"))
        username = str(from_user.get("username", "")).strip() or user_id

        if not self._is_authorized(chat_id, ""):
            self._answer_callback_query(callback_query_id, "Unauthorized")
            self._send_message(chat_id, "Unauthorized chat. Access denied.")
            return

        action_key = f"{chat_id}:cb:{data.lower()}"
        if self._is_duplicate_action(action_key):
            self._answer_callback_query(callback_query_id, "Duplicate tap ignored.")
            return

        try:
            reply = self._callback_handler(chat_id, user_id, username, data, message_id)
        except Exception as exc:
            logger.exception("Telegram callback handler failure")
            reply = f"Command failed: {exc}"

        notice: str | None = None
        if isinstance(reply, TelegramReply):
            notice = reply.callback_notice
        self._answer_callback_query(callback_query_id, notice)
        self._send_reply(chat_id, reply)

    def _send_reply(self, chat_id: int, reply: str | TelegramReply) -> int | None:
        if isinstance(reply, TelegramReply):
            if reply.edit_message_id:
                self._edit_message(
                    chat_id,
                    reply.edit_message_id,
                    reply.text,
                    inline_keyboard=reply.inline_keyboard,
                    html=reply.html,
                )
                return reply.edit_message_id
            return self._send_message(chat_id, reply.text, inline_keyboard=reply.inline_keyboard, html=reply.html)
        return self._send_message(chat_id, reply)
