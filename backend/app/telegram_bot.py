import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, parse, request

logger = logging.getLogger(__name__)


@dataclass
class TelegramReply:
    text: str
    inline_keyboard: list[list[dict[str, str]]] | None = None
    edit_message_id: int | None = None
    callback_notice: str | None = None


CommandHandler = Callable[[int, str, str, str], str | TelegramReply]
CallbackHandler = Callable[[int, str, str, str, int], str | TelegramReply]


@dataclass
class TelegramBotStatus:
    enabled: bool
    polling: bool
    alerts_enabled: bool
    authorized_chats: int
    detail: str


class TelegramBotService:
    def __init__(
        self,
        *,
        token: str,
        allowed_chat_ids: set[int],
        alerts_enabled: bool,
        command_handler: CommandHandler,
        callback_handler: CallbackHandler,
        # Whether this process should be the one polling. Injected and
        # defaulted so existing construction sites and tests keep working
        # without Redis.
        is_leader: Callable[[], bool] = lambda: True,
    ) -> None:
        self._token = token.strip()
        self._allowed_chat_ids = set(allowed_chat_ids)
        self._alerts_enabled = alerts_enabled
        self._command_handler = command_handler
        self._callback_handler = callback_handler
        self._is_leader = is_leader
        self._offset = 0
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._recent_actions: dict[str, float] = {}
        self._dedupe_ttl_seconds = 15.0
        self._polling = False
        self._detail = "Telegram bot is not running"

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def status(self) -> TelegramBotStatus:
        with self._lock:
            return TelegramBotStatus(
                enabled=self.enabled,
                polling=self._polling,
                alerts_enabled=self._alerts_enabled,
                authorized_chats=len(self._allowed_chat_ids),
                detail=self._detail,
            )

    def start(self) -> None:
        if not self.enabled:
            with self._lock:
                self._detail = "Telegram bot disabled: TELEGRAM_BOT_TOKEN is missing"
            return
        if self._thread and self._thread.is_alive():
            return
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

    def notify(self, text: str) -> None:
        if not self._alerts_enabled:
            return
        safe_text = text.strip()
        if not safe_text:
            return
        for chat_id in self._allowed_chat_ids:
            self._send_message(chat_id, safe_text)

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    def _post_json(self, method: str, payload: dict) -> dict:
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._api_url(method),
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=35) as resp:
            body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(f"Telegram API error: {parsed!r}")
        return parsed

    def _send_message(
        self,
        chat_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> None:
        try:
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "text": text[:3900],
                "disable_web_page_preview": True,
            }
            if inline_keyboard:
                payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
            self._post_json(
                "sendMessage",
                payload,
            )
        except Exception as exc:
            logger.warning("Failed to send Telegram message to chat_id=%s: %s", chat_id, exc)

    def _edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        inline_keyboard: list[list[dict[str, str]]] | None = None,
    ) -> None:
        try:
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text[:3900],
                "disable_web_page_preview": True,
            }
            if inline_keyboard:
                payload["reply_markup"] = {"inline_keyboard": inline_keyboard}
            self._post_json("editMessageText", payload)
        except Exception as exc:
            logger.warning(
                "Failed to edit Telegram message chat_id=%s message_id=%s: %s",
                chat_id,
                message_id,
                exc,
            )

    def _answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        try:
            payload: dict[str, Any] = {"callback_query_id": callback_query_id}
            if text:
                payload["text"] = text[:180]
            self._post_json("answerCallbackQuery", payload)
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

        if chat_id not in self._allowed_chat_ids:
            self._send_message(chat_id, "Unauthorized chat. Access denied.")
            return

        action_key = f"{chat_id}:{text.lower()}"
        if self._is_duplicate_action(action_key):
            self._send_message(chat_id, "Duplicate command ignored (tap detected twice).")
            return

        try:
            reply = self._command_handler(chat_id, user_id, username, text)
        except Exception as exc:
            logger.exception("Telegram command handler failure")
            reply = f"Command failed: {exc}"
        self._send_reply(chat_id, reply)

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

        if chat_id not in self._allowed_chat_ids:
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

    def _send_reply(self, chat_id: int, reply: str | TelegramReply) -> None:
        if isinstance(reply, TelegramReply):
            if reply.edit_message_id:
                self._edit_message(
                    chat_id,
                    reply.edit_message_id,
                    reply.text,
                    inline_keyboard=reply.inline_keyboard,
                )
            else:
                self._send_message(chat_id, reply.text, inline_keyboard=reply.inline_keyboard)
            return
        self._send_message(chat_id, reply)
