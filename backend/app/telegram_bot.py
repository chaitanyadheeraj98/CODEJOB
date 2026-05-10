import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib import error, parse, request

logger = logging.getLogger(__name__)


CommandHandler = Callable[[int, str, str, str], str]


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
    ) -> None:
        self._token = token.strip()
        self._allowed_chat_ids = set(allowed_chat_ids)
        self._alerts_enabled = alerts_enabled
        self._command_handler = command_handler
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

    def _send_message(self, chat_id: int, text: str) -> None:
        try:
            self._post_json(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": text[:3900],
                    "disable_web_page_preview": True,
                },
            )
        except Exception as exc:
            logger.warning("Failed to send Telegram message to chat_id=%s: %s", chat_id, exc)

    def _poll_updates(self) -> list[dict]:
        payload = {
            "timeout": 30,
            "allowed_updates": ["message"],
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
        while self._running:
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
        self._send_message(chat_id, reply)
