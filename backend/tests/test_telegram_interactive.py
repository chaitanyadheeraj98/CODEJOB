import os
import unittest
from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["DEBUG"] = "false"

from app import main
from app.config import settings
from app.db import Base
from app.models import RecruiterEmail, User, UserSettings
from app.schemas import AIStatusResponse, AutomationRunResponse, EmailResponse
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.telegram_bot import TelegramBotService, TelegramReply


class TelegramInteractiveMainTests(unittest.TestCase):
    def setUp(self) -> None:
        main.telegram_pending_inputs.clear()

    def test_start_returns_main_menu_reply(self) -> None:
        reply = main._handle_telegram_command(123, "u1", "tester", "/start")
        self.assertIsInstance(reply, TelegramReply)
        typed = reply if isinstance(reply, TelegramReply) else None
        self.assertIsNotNone(typed)
        self.assertIn("Choose a section", typed.text)
        self.assertIsNotNone(typed.inline_keyboard)

    def test_callback_menu_edit_response(self) -> None:
        reply = main._handle_telegram_callback(123, "u1", "tester", "menu:config:0", 55)
        self.assertIsInstance(reply, TelegramReply)
        typed = reply if isinstance(reply, TelegramReply) else None
        self.assertIsNotNone(typed)
        self.assertEqual(typed.edit_message_id, 55)
        self.assertIn("Config menu", typed.text)
        self.assertIsNotNone(typed.inline_keyboard)

    def test_flow_sets_pending_and_cancel_clears(self) -> None:
        reply = main._handle_telegram_callback(123, "u1", "tester", "flow:await_setquery", 21)
        self.assertIsInstance(reply, TelegramReply)
        self.assertEqual(main.telegram_pending_inputs.get(123), "await_setquery")
        cancel = main._handle_telegram_callback(123, "u1", "tester", "cancel:pending", 21)
        self.assertIsInstance(cancel, TelegramReply)
        self.assertNotIn(123, main.telegram_pending_inputs)

    def test_pagination_has_more_button(self) -> None:
        buttons = [main._tg_btn(f"Item {i}", f"cmd:/x{i}") for i in range(8)]
        rows = main._telegram_paginate_buttons(buttons, 0, menu_action="config", include_home=True, include_back=True)
        flat = [button["text"] for row in rows for button in row]
        self.assertIn("More", flat)


class TelegramBotServiceCallbackTests(unittest.TestCase):
    def test_callback_update_is_processed(self) -> None:
        sent: list[tuple[str, dict]] = []
        callback_calls: list[str] = []

        def fake_post(method: str, payload: dict) -> dict:
            sent.append((method, payload))
            return {"ok": True, "result": []}

        def command_handler(chat_id: int, user_id: str, username: str, text: str) -> str:
            _ = (chat_id, user_id, username)
            return f"echo:{text}"

        def callback_handler(chat_id: int, user_id: str, username: str, data: str, message_id: int) -> TelegramReply:
            _ = (chat_id, user_id, username, message_id)
            callback_calls.append(data)
            return TelegramReply(text="updated", edit_message_id=42, callback_notice="ok")

        service = TelegramBotService(
            token="x",
            allowed_chat_ids={999},
            alerts_enabled=True,
            command_handler=command_handler,
            callback_handler=callback_handler,
        )
        service._post_json = fake_post  # type: ignore[method-assign]
        update = {
            "callback_query": {
                "id": "cb1",
                "data": "menu:main:0",
                "from": {"id": 1, "username": "user1"},
                "message": {"message_id": 42, "chat": {"id": 999}},
            }
        }
        service._handle_update(update)
        self.assertEqual(callback_calls, ["menu:main:0"])
        methods = [item[0] for item in sent]
        self.assertIn("answerCallbackQuery", methods)
        self.assertIn("editMessageText", methods)


class TelegramReviewCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, future=True)
        self._candidate_seq = 0
        with self.session_factory() as db:
            db.add(
                UserSettings(
                    owner_id="default-owner",
                    gmail_query="is:unread",
                    default_gmail_query="is:unread",
                )
            )
            db.commit()
        self.runtime = TelegramRuntime(self._deps())

    def _deps(self) -> TelegramRuntimeDeps:
        return TelegramRuntimeDeps(
            session_factory=self.session_factory,
            get_settings=lambda db: db.query(UserSettings).filter(UserSettings.owner_id == "default-owner").first(),
            read_policy_from_settings=lambda _settings: {},
            policy_dry_run=lambda _policy: False,
            format_query_preflight=lambda _settings, _policy: "preflight",
            poll_interval_minutes=lambda _settings: 10,
            build_telegram_digest=lambda prefix, _result: prefix,
            gmail_auth_status=lambda: (False, False, "not configured"),
            ai_status=lambda: AIStatusResponse(
                configured=False,
                connected=False,
                running=False,
                provider="deepseek",
                model="deepseek-chat",
                detail="offline",
                embedding_provider="hash",
                embedding_model="text-embedding-3-small",
                embedding_connected=False,
                embedding_detail="offline",
            ),
            gmail_sync=lambda _db: None,
            automation_run_once=lambda _payload, _db: AutomationRunResponse(status="idle", detail="idle"),
            get_candidate_review=lambda email_id, db: main._get_candidate_review(email_id, db),
            approve_and_send=lambda _email_id, _payload, _db: None,
            reject_candidate=lambda _email_id, _payload, _db: None,
            resolve_owner=lambda _chat_id: "default-owner",
            redeem_link_code=lambda _code, _chat_id, _user_id, _username: None,
            action_lock=main.telegram_action_lock,
            verify_action_pin=lambda _owner_id, _pin: True,
            auth_ttl_minutes=lambda: 30,
        )

    def _add_candidate(self, **overrides: object) -> RecruiterEmail:
        self._candidate_seq += 1
        suffix = str(self._candidate_seq)
        payload = {
            "owner_id": "default-owner",
            "sender": "shubham.sonkar@gvrinfotek.com",
            "subject": "MongoDB Engineer with Atlas (Cloud Migration) || REMOTE || SKYPE",
            "body": "Hello recruiter body",
            "role": "MongoDB Engineer",
            "location": "Remote",
            "salary_text": "$70/hr",
            "skills_text": "mongodb, atlas, migration",
            "decision": "Qualified",
            "state": "needs_review",
            "decision_reason": "Qualified and queued for manual approval",
            "draft_reply": "Hi, I am interested in this role.",
            "draft_source": "deepseek",
            "draft_model": "deepseek-chat",
            "draft_resume_context_status": "injected",
            "approval_status": "pending",
            "sent_status": "not_sent",
            "source": "nvoids",
            "external_message_id": f"nvoids:{12345 + self._candidate_seq}",
            "external_thread_id": f"https://nvoids.com/job_details.jsp?id={12345 + self._candidate_seq}",
            "recipient_email": "shubham.sonkar@gvrinfotek.com",
            "cc_email": "alekya@rpatechnologyinc.com",
            "routing_status": "safe",
            "routing_confidence": 0.85,
            "routing_reason": "External feed recruiter import with employer pool cc.",
            "routing_confirmed": True,
            "resume_file_name": "Resume.docx",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
        payload.update(overrides)
        with self.session_factory() as db:
            row = RecruiterEmail(**payload)
            db.add(row)
            db.commit()
            db.refresh(row)
            return row

    def test_review_command_returns_rich_candidate_details(self) -> None:
        row = self._add_candidate(draft_ai_error="fallback used", last_error="sheet warning")
        reply = self.runtime.handle_command(123, "u1", "tester", f"/review {row.id}")

        self.assertIsInstance(reply, str)
        text = str(reply)
        self.assertIn(f"Email ID: {row.id}", text)
        self.assertIn(f"Source Listing: {row.external_thread_id}", text)
        self.assertIn("To: shubham.sonkar@gvrinfotek.com", text)
        self.assertIn("CC: alekya@rpatechnologyinc.com", text)
        self.assertIn("Routing: safe (85%)", text)
        self.assertIn("Draft source: DeepSeek (deepseek-chat)", text)
        self.assertIn("Resume Context: Injected", text)
        self.assertIn("AI fallback: fallback used", text)
        self.assertIn("Last Error: sheet warning", text)
        self.assertIn("Draft Preview:", text)

    def test_review_command_rejects_non_review_candidate(self) -> None:
        row = self._add_candidate(state="approved_sent", sent_status="sent", approval_status="approved")
        reply = self.runtime.handle_command(123, "u1", "tester", f"/review {row.id}")

        self.assertEqual(
            reply,
            "Only needs_review candidates can be reviewed from Telegram. Current state: approved_sent",
        )

    def test_review_command_reports_missing_candidate(self) -> None:
        reply = self.runtime.handle_command(123, "u1", "tester", "/review 99999")
        self.assertEqual(reply, "Command failed (404): Candidate not found")

    def test_needs_review_stays_compact(self) -> None:
        first = self._add_candidate(subject="First candidate subject")
        self._add_candidate(subject="Second candidate subject")
        reply = self.runtime.handle_command(123, "u1", "tester", "/needs_review")

        self.assertIsInstance(reply, str)
        text = str(reply)
        self.assertIn("Needs Review: 2", text)
        self.assertIn(f"#{first.id} - First candidate subject", text)
        self.assertNotIn("To:", text)
        self.assertNotIn("Draft Preview:", text)

    def test_a_deactivated_owner_cannot_run_telegram_commands(self) -> None:
        with self.session_factory() as db:
            db.add(User(
                owner_id="default-owner",
                email="disabled@example.com",
                disabled_at=datetime.now(UTC),
            ))
            db.commit()
        previous = settings.feature_auth_enabled
        settings.feature_auth_enabled = True
        try:
            reply = self.runtime.handle_command(123, "u1", "tester", "/needs_review")
        finally:
            settings.feature_auth_enabled = previous

        self.assertEqual(reply, "Account is deactivated.")

    def test_review_message_truncates_long_draft_preview(self) -> None:
        candidate = EmailResponse.model_validate(
            self._add_candidate(
                draft_reply="Paragraph " * 200,
                subject="X" * 220,
                routing_reason="Reason " * 80,
            )
        )

        text = TelegramRuntime._format_review_message(candidate)

        self.assertIn("[truncated]", text)
        self.assertLess(len(text), 2000)


if __name__ == "__main__":
    unittest.main()
