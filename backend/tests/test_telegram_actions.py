from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import UserSettings
from app.runtime_state import runtime_state
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.telegram_bot import TelegramReply


def _candidate(email_id: int = 9):
    return SimpleNamespace(
        id=email_id,
        sender="sender@example.com",
        subject="Role <Lead> & Platform",
        draft_reply="Interested",
        routing_reason="Verified",
        source="gmail",
        external_thread_id=None,
        external_message_id=None,
        gmail_message_url=None,
        recipient_email="recruiter@example.com",
        cc_email=None,
        routing_status="safe",
        routing_confidence=1.0,
        resume_file_name="Resume.docx",
        draft_source="deepseek",
        draft_model="model",
        draft_resume_context_status="injected",
        draft_ai_error=None,
        last_error=None,
        state="needs_review",
    )


def _runtime():
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    with factory() as db:
        db.add(UserSettings(owner_id="owner-a", gmail_query="is:unread", default_gmail_query="is:unread"))
        db.commit()
    approved: list[int] = []
    rejected: list[int] = []
    runtime = TelegramRuntime(
        TelegramRuntimeDeps(
            session_factory=factory,
            get_settings=lambda db: db.query(UserSettings).filter_by(owner_id="owner-a").one(),
            read_policy_from_settings=lambda _settings: {},
            policy_dry_run=lambda _policy: False,
            format_query_preflight=lambda _settings, _policy: "",
            poll_interval_minutes=lambda _settings: 10,
            build_telegram_digest=lambda prefix, _result: prefix,
            gmail_auth_status=lambda: (False, False, ""),
            ai_status=lambda: SimpleNamespace(connected=False, model="auto"),
            gmail_sync=lambda _db: None,
            automation_run_once=lambda _payload, _db: None,
            get_candidate_review=lambda email_id, _db: _candidate(email_id),
            approve_and_send=lambda email_id, _payload, _db: approved.append(email_id) or SimpleNamespace(id=email_id, subject="Role"),
            reject_candidate=lambda email_id, _payload, _db: rejected.append(email_id) or SimpleNamespace(id=email_id, decision_reason="Rejected from Telegram"),
            resolve_owner=lambda _chat_id: "owner-a",
            redeem_link_code=lambda _code, _chat_id, _user_id, _username: None,
            action_lock=__import__("threading").Lock(),
            verify_action_pin=lambda _owner_id, pin: pin == "1234",
            auth_ttl_minutes=lambda: 30,
        )
    )
    return runtime, approved, rejected, engine


def test_first_approve_tap_only_opens_confirmation() -> None:
    runtime, approved, _rejected, engine = _runtime()
    try:
        reply = runtime.handle_callback(11, "u", "name", "act:approve:9", 44)
        assert isinstance(reply, TelegramReply)
        assert reply.edit_message_id == 44
        assert approved == []
        assert "Confirm send?" in reply.text
    finally:
        engine.dispose()


def test_confirm_approve_matches_typed_approve_and_edits_in_place() -> None:
    runtime, approved, _rejected, engine = _runtime()
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        reply = runtime.handle_callback(11, "u", "name", "act:go:approve:9", 44)
        assert isinstance(reply, TelegramReply)
        assert reply.edit_message_id == 44
        assert approved == [9]
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_confirm_without_auth_remembers_action_without_leaking_candidate() -> None:
    runtime, approved, _rejected, engine = _runtime()
    runtime_state.telegram_auth_sessions.pop(11, None)
    try:
        reply = runtime.handle_callback(11, "u", "name", "act:go:approve:9", 44)
        assert isinstance(reply, TelegramReply)
        assert approved == []
        assert "Role" not in reply.text
        assert runtime_state.telegram_pending_inputs[11] == "await_auth_pin|act:go:approve:9|44"

        completed = runtime.handle_command(11, "u", "name", "1234")
        assert isinstance(completed, TelegramReply)
        assert completed.edit_message_id == 44
        assert approved == [9]
    finally:
        runtime_state.telegram_pending_inputs.pop(11, None)
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_reject_confirmation_and_callback_size_limit() -> None:
    runtime, _approved, rejected, engine = _runtime()
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:reject:{2**31}", 44)
        assert isinstance(reply, TelegramReply)
        assert rejected == []
        for row in reply.inline_keyboard or []:
            for button in row:
                assert len(button["callback_data"].encode()) <= 64
    finally:
        engine.dispose()
