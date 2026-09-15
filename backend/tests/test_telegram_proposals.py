import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ChatMessage, ChatSession, UserSettings
from app.runtime_state import runtime_state
from app.services.chat_service import ChatService
from app.services.proposal_actions import PROPOSAL_ACTIONS
from app.services.telegram_format import EMAIL_PROPOSAL_TOOLS, email_proposal, plain_text, proposal_card
from app.services.telegram_runtime_service import TelegramRuntime, TelegramRuntimeDeps
from app.telegram_bot import TelegramReply


def _payload(**overrides):
    value = {
        "action": "send_email",
        "candidate_email_id": 42,
        "to": "recruiter@example.com",
        "cc": "",
        "subject": "Re: Platform <Lead>",
        "body": "Attached, thanks.",
        "document_ids": [3, 7, 9],
        "document_names": ["Resume.pdf", "Portfolio.docx", "References.pdf"],
    }
    value.update(overrides)
    return value


def _runtime(payload=None):
    engine = create_engine("sqlite+pysqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, future=True)
    sent: list[tuple[int, object]] = []
    composed: list[object] = []
    with factory() as db:
        db.add(UserSettings(owner_id="owner-a", gmail_query="is:unread", default_gmail_query="is:unread"))
        session = ChatSession(owner_id="owner-a", origin="telegram")
        db.add(session)
        db.flush()
        proposal = ChatMessage(
            session_id=session.id,
            role="tool",
            tool_name=(
                "propose_new_email"
                if (payload or {}).get("action") == "send_new_email"
                else "propose_send_email"
            ),
            content=json.dumps(payload if payload is not None else _payload()),
        )
        db.add(proposal)
        db.commit()
        proposal_id = proposal.id
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
            get_candidate_review=lambda _id, _db: None,
            approve_and_send=lambda _id, _payload, _db: None,
            reject_candidate=lambda _id, _payload, _db: None,
            resolve_owner=lambda _chat_id: "owner-a",
            redeem_link_code=lambda _code, _chat_id, _user_id, _username: None,
            action_lock=__import__("threading").Lock(),
            verify_action_pin=lambda _owner, _pin: True,
            auth_ttl_minutes=lambda: 30,
            send_chat_reply=lambda email_id, request, _db: sent.append((email_id, request)) or {"sent": True},
            send_chat_new_email=lambda request, _db: composed.append(request) or {"sent": True},
            record_proposal_outcome=ChatService().record_proposal_outcome,
        )
    )
    return runtime, factory, engine, proposal_id, sent, composed


def test_renderer_names_every_document_and_escapes_content() -> None:
    rendered = email_proposal(json.dumps(_payload()), 15)
    assert rendered is not None
    text, keyboard = rendered
    visible = plain_text(text)
    assert all(name in visible for name in _payload()["document_names"])
    assert "<Lead>" in visible
    assert keyboard and keyboard[0][0]["callback_data"] == "act:prop:send:15"


def test_malformed_or_incomplete_payload_renders_nothing() -> None:
    assert email_proposal("not json", 1) is None
    assert email_proposal(json.dumps(_payload(document_names=["only one"])), 1) is None
    assert email_proposal(json.dumps(_payload(body="")), 1) is None


def test_missing_fields_payload_is_a_question_without_buttons() -> None:
    rendered = email_proposal(json.dumps({"status": "missing_fields", "missing": ["body"]}), 1)
    assert rendered == ("I need body before I can prepare that email.", None)


GENERIC_PAYLOADS = {
    "propose_candidate_action": {
        "action": "propose_candidate_action",
        "candidate_action": "track",
        "label": "Track",
        "candidate_ids": [1],
        "count": 1,
        "reversible": True,
        "reversible_detail": "Can be undone.",
    },
    "propose_bulk_approve_candidates": {
        "action": "approve_candidates",
        "candidate_ids": [9675],
        "count": 1,
    },
    "propose_record_update": {
        "action": "propose_record_update",
        "record_kind": "application",
        "record_id": 2,
        "record_label": "Platform Lead",
        "fields": {"status": "interview_1"},
        "changes": [{"field": "status", "from": "applied", "to": "interview_1"}],
    },
    "propose_track_record": {
        "action": "propose_track_record",
        "record_kind": "requirement",
        "record_label": "Platform role",
        "recruiter": "Pat",
        "labels": ["Jobs"],
        "resume": "Resume.pdf",
        "fields": {"resume_asset_id": 1, "recruiter_email_id": 2, "dedupe_key": "key"},
    },
    "propose_add_note": {
        "action": "propose_add_note",
        "record_kind": "application",
        "record_id": 2,
        "record_label": "Platform role",
        "note": "Followed up",
    },
    "propose_create_premium_contact": {
        "action": "create_premium_contact",
        "fields": {"name": "Pat", "phone": "2145551212", "role": "recruiter"},
    },
    "propose_manual_requirement": {
        "action": "propose_manual_requirement",
        "source_label": "message 3",
        "message_id": 3,
        "characters": 120,
        "jd_text": "Build APIs",
    },
    "propose_nvoids_search": {
        "action": "propose_nvoids_search",
        "company": "Acme",
        "criteria": {"end_client": "Acme", "query_mode": "composed", "batch_limit": 10},
        "already_stored": 2,
    },
    "propose_taxonomy_bulk_review": {
        "action": "propose_taxonomy_bulk_review",
        "scope": "skill",
        "taxonomy_action": "approve",
        "label": "Approve",
        "keys": ["python"],
        "sample_names": ["Python"],
        "reversible": True,
    },
    "propose_scheduled_task": {
        "action": "propose_scheduled_task",
        "operation": "create",
        "title": "Follow up",
        "kind": "reminder",
        "trigger": "tomorrow",
        "permitted_actions": "notify",
        "reversible": True,
    },
    "propose_profile_update": {
        "action": "propose_profile_update",
        "operation": "append",
        "field": "Location",
        "value": "Austin",
        "entry": "Location: Austin",
        "resulting_profile": "Location: Austin",
    },
    "propose_resume_draft": {
        "action": "propose_resume_draft",
        "name": "Platform draft",
        "source_variant_code": "R01",
        "source_file_name": "Resume.pdf",
        "source_characters": 5000,
    },
    "propose_resume_section": {
        "action": "propose_resume_section",
        "draft_id": 4,
        "draft_name": "Platform draft",
        "section": "Summary",
        "current": "Built APIs",
        "replacement": "Built reliable APIs",
    },
    "propose_create_github_issue": {
        "action": "create_github_issue",
        "title": "Card missing",
        "user_report": "I do not see the card",
        "ai_summary": "Telegram omitted a proposal card",
    },
}


def test_every_generic_proposal_renders_a_summary_and_two_buttons() -> None:
    assert set(GENERIC_PAYLOADS) == set(PROPOSAL_ACTIONS) - EMAIL_PROPOSAL_TOOLS
    for tool_name, payload in GENERIC_PAYLOADS.items():
        rendered = proposal_card(tool_name, json.dumps(payload), 91)
        assert rendered is not None, tool_name
        text, keyboard = rendered
        assert "Action ready to confirm" in plain_text(text), tool_name
        assert PROPOSAL_ACTIONS[tool_name].summary(payload), tool_name
        assert keyboard is not None and len(keyboard[0]) == 2, tool_name


def test_generic_missing_fields_is_a_question_without_buttons() -> None:
    rendered = proposal_card(
        "propose_add_note",
        json.dumps({"status": "missing_fields", "missing": ["note"]}),
        1,
    )
    assert rendered == ("I need note before I can prepare that action.", None)


def test_generic_error_is_a_refusal_without_buttons() -> None:
    rendered = proposal_card("propose_add_note", json.dumps({"error": "Record not found"}), 1)
    assert rendered is not None
    assert "nothing has happened" in plain_text(rendered[0])
    assert rendered[1] is None


def test_generic_summary_escapes_untrusted_fields() -> None:
    payload = {**GENERIC_PAYLOADS["propose_add_note"], "note": "<b>do not trust me</b>"}
    rendered = proposal_card("propose_add_note", json.dumps(payload), 1)
    assert rendered is not None
    assert "&lt;b&gt;do not trust me&lt;/b&gt;" in rendered[0]


def test_candidate_action_card_shows_reversibility() -> None:
    rendered = proposal_card(
        "propose_candidate_action",
        json.dumps(GENERIC_PAYLOADS["propose_candidate_action"]),
        1,
    )
    assert rendered is not None
    assert "Reversible: Yes" in plain_text(rendered[0])


def test_confirm_sends_and_records_event() -> None:
    runtime, factory, engine, proposal_id, sent, _composed = _runtime()
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert reply.edit_message_id == 55
        assert sent[0][0] == 42
        assert sent[0][1].document_ids == [3, 7, 9]
        with factory() as db:
            event = db.query(ChatMessage).filter_by(role="event").one()
            assert json.loads(event.tool_call_args)["outcome"] == "confirmed"
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_confirm_requires_an_active_auth_session() -> None:
    runtime, _factory, engine, proposal_id, sent, _composed = _runtime()
    runtime_state.telegram_auth_sessions.pop(11, None)
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert sent == []
        assert "PIN" in reply.text
    finally:
        runtime_state.telegram_pending_inputs.pop(11, None)
        engine.dispose()


def test_cancel_sends_nothing_and_records_event() -> None:
    runtime, factory, engine, proposal_id, sent, _composed = _runtime()
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:cancel:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert reply.edit_message_id == 55
        assert sent == []
        with factory() as db:
            event = db.query(ChatMessage).filter_by(role="event").one()
            assert json.loads(event.tool_call_args)["outcome"] == "cancelled"
    finally:
        engine.dispose()


def test_card_names_an_unknown_recipient() -> None:
    rendered = email_proposal(
        json.dumps(_payload(unknown_recipients=["stranger@elsewhere.com"])), 15
    )
    assert rendered is not None
    # Named rather than counted: "one unknown recipient" is not a thing a user
    # on a phone can check, and the address is.
    assert "stranger@elsewhere.com" in plain_text(rendered[0])


def test_first_send_tap_asks_before_it_sends_to_a_stranger() -> None:
    runtime, _factory, engine, proposal_id, sent, _composed = _runtime(
        _payload(unknown_recipients=["stranger@elsewhere.com"])
    )
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert sent == []
        assert "stranger@elsewhere.com" in plain_text(reply.text)
        assert reply.inline_keyboard[0][0]["callback_data"] == f"act:prop:force:{proposal_id}"
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_the_second_tap_carries_the_confirmation_the_first_cannot() -> None:
    runtime, _factory, engine, proposal_id, sent, _composed = _runtime(
        _payload(unknown_recipients=["stranger@elsewhere.com"])
    )
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        runtime.handle_callback(11, "u", "name", f"act:prop:force:{proposal_id}", 55)
        assert sent[0][1].confirm_new_recipients is True
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_send_anyway_still_needs_an_auth_session() -> None:
    # The extra confirmation is about *who*, not *whether*. It must not become
    # a way around the PIN.
    runtime, _factory, engine, proposal_id, sent, _composed = _runtime(
        _payload(unknown_recipients=["stranger@elsewhere.com"])
    )
    runtime_state.telegram_auth_sessions.pop(11, None)
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:force:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert sent == []
        assert "PIN" in reply.text
    finally:
        runtime_state.telegram_pending_inputs.pop(11, None)
        engine.dispose()


def test_a_known_recipient_still_sends_on_the_first_tap() -> None:
    runtime, _factory, engine, proposal_id, sent, _composed = _runtime()
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert sent[0][1].confirm_new_recipients is False
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def _new_email_payload(**overrides):
    value = {
        "action": "send_new_email",
        "to": "xyz@example.com",
        "cc": "",
        "subject": "Contract rate",
        "body": "Can we discuss?",
        "document_ids": [],
        "document_names": [],
        "unknown_recipients": ["xyz@example.com"],
        "requires_recipient_confirmation": True,
    }
    value.update(overrides)
    return value


def test_a_composed_email_goes_to_the_new_email_sender() -> None:
    # Routed on the payload's action, not the button: the two cards carry the
    # same Send, and only one of them has a thread to reply to.
    runtime, _factory, engine, proposal_id, sent, composed = _runtime(_new_email_payload())
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        runtime.handle_callback(11, "u", "name", f"act:prop:force:{proposal_id}", 55)
        assert sent == []
        assert composed[0].to == "xyz@example.com"
        assert composed[0].subject == "Contract rate"
        assert composed[0].confirm_new_recipients is True
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_composing_to_a_stranger_still_asks_first() -> None:
    runtime, _factory, engine, proposal_id, _sent, composed = _runtime(_new_email_payload())
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        reply = runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert isinstance(reply, TelegramReply)
        assert composed == []
        assert reply.inline_keyboard[0][0]["callback_data"] == f"act:prop:force:{proposal_id}"
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_composing_to_a_known_address_sends_on_the_first_tap() -> None:
    runtime, _factory, engine, proposal_id, _sent, composed = _runtime(
        _new_email_payload(to="pat@acme.com", unknown_recipients=[], requires_recipient_confirmation=False)
    )
    runtime_state.telegram_auth_sessions[11] = datetime.now(UTC) + timedelta(minutes=5)
    try:
        runtime.handle_callback(11, "u", "name", f"act:prop:send:{proposal_id}", 55)
        assert composed[0].to == "pat@acme.com"
        assert composed[0].confirm_new_recipients is False
    finally:
        runtime_state.telegram_auth_sessions.pop(11, None)
        engine.dispose()


def test_cancelling_a_composed_email_sends_nothing() -> None:
    runtime, factory, engine, proposal_id, _sent, composed = _runtime(_new_email_payload())
    try:
        runtime.handle_callback(11, "u", "name", f"act:prop:cancel:{proposal_id}", 55)
        assert composed == []
        with factory() as db:
            event = db.query(ChatMessage).filter_by(role="event").one()
            assert json.loads(event.tool_call_args)["outcome"] == "cancelled"
    finally:
        engine.dispose()
