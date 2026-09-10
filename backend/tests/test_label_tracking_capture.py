from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import EmailConversation, EmailReplyMessage, GmailLabel, RecruiterEmail
from app.services import email_inbox_service as inbox, label_tracking_service as tracking


def test_capture_is_idempotent_and_preserves_gmail_unread_direction():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        item = dict(external_message_id="m1", external_thread_id="t1", sender="Naman <naman@valzosoft.com>",
            subject="RTR", body="Please confirm", to_header="me@gmail.com", cc_header="team@valzosoft.com",
            label_ids=["Label_1"], gmail_received_at=datetime.now(UTC))
        conv = inbox.ensure_label_conversation(db, owner_id="a", thread_id="t1", label_external_id="Label_1", first_item=item)
        assert inbox.capture_labeled_message(db, owner_id="a", item=item, owner_email="me@gmail.com", conversation=conv)
        assert not inbox.capture_labeled_message(db, owner_id="a", item=item, owner_email="me@gmail.com", conversation=conv)
        assert conv.unread_reply_count == 0
        assert inbox.capture_labeled_message(db, owner_id="a", item={**item, "external_message_id": "m2", "label_ids": ["UNREAD"]}, owner_email="me@gmail.com", conversation=conv)
        assert conv.unread_reply_count == 1
        inbox.capture_labeled_message(db, owner_id="a", item={**item, "external_message_id": "m3", "sender": "me@gmail.com", "label_ids": ["UNREAD"]}, owner_email="me@gmail.com", conversation=conv)
        db.flush()
        assert conv.unread_reply_count == 1
        assert db.query(EmailReplyMessage).filter_by(external_message_id="m3").one().direction == "outbound"
        assert db.query(RecruiterEmail).count() == 0
        assert inbox.conversation_detail(db, "a", conv.id).subject == "RTR"
        assert inbox.mark_conversation_read(db, "a", conv.id).unread_reply_count == 0
        send = Mock(return_value="sent-id")
        inbox.send_conversation_reply(db, owner_id="a", conversation_id=conv.id, body="Yes", sender="me@gmail.com", draft_text_size="normal", tracking_url=None, send_reply=send)
        assert send.call_args.args[1] == "naman@valzosoft.com"
    engine.dispose()


def test_sync_and_watch_capture_create_rootless_conversations():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(GmailLabel(owner_id="a", external_label_id="Label_1", name="RTR", is_tracked=True))
        db.flush()
        item = dict(external_message_id="m", external_thread_id="t", sender="naman@valzosoft.com", subject="RTR", body="Reply", label_ids=["UNREAD"])
        deps = SimpleNamespace(list_candidates_by_label_ids=Mock(return_value=[item]))
        result = tracking.sync_tracked_labels(db, "a", deps=deps, owner_email="me@gmail.com")
        assert result.messages == result.threads == 1
        assert inbox.list_conversations(db, "a", label="RTR", unread_only=True)[0].labels == ["RTR"]
        assert inbox.list_conversations(db, "a", role="Java") == []
        assert inbox.list_conversations(db, "a", label="unknown") == []
        assert inbox.list_conversations(db, "b") == []
        assert inbox.list_conversations(db, "a", recruiter="naman", subject="RTR")[0].root_recruiter_email_id is None
        watch = SimpleNamespace(id=1, origin_label_external_id="Label_1")
        conv = inbox.ensure_watch_conversation(db, owner_id="a", thread_id="new", watch=watch, first_item=item)
        assert conv.origin == "watch"
        inbox.capture_labeled_message(db, owner_id="a", item={**item, "external_message_id": "new-msg"}, owner_email="me@gmail.com", conversation=conv, matched_watch_id=1)
        db.flush()
        assert db.query(EmailReplyMessage).filter_by(external_message_id="new-msg").one().matched_watch_id == 1
    engine.dispose()


def test_a_failed_reply_scan_does_not_cancel_label_tracking():
    """Gmail answers this account's reply scan with 403 rateLimitExceeded often
    enough that the label half never ran: the refresh returned before it. Label
    tracking is an independent source of threads, so it runs either way, and the
    failure is still reported rather than swallowed."""
    from fastapi import HTTPException

    from app.services.orchestration_service import OrchestrationService

    service = OrchestrationService.__new__(OrchestrationService)
    user_settings = SimpleNamespace(enabled=True)
    service.deps = SimpleNamespace(
        owner_id="a",
        is_gmail_configured=lambda: True,
        get_settings=lambda _db: user_settings,
    )
    db = Mock()
    label_sync = Mock()

    with patch.object(OrchestrationService, "_capture_inbound_replies", side_effect=RuntimeError("403 rateLimitExceeded")), \
         patch.object(OrchestrationService, "_sync_label_tracking", label_sync), \
         patch("app.services.orchestration_service.list_conversations", return_value=[]):
        with pytest.raises(HTTPException) as caught:
            service.refresh_inbox_replies(db)

    label_sync.assert_called_once()
    assert caught.value.status_code == 502
    assert "labeled threads were still refreshed" in caught.value.detail
    db.rollback.assert_called_once()


def test_a_healthy_refresh_still_returns_the_conversations():
    from app.services.orchestration_service import OrchestrationService

    service = OrchestrationService.__new__(OrchestrationService)
    service.deps = SimpleNamespace(
        owner_id="a",
        is_gmail_configured=lambda: True,
        get_settings=lambda _db: SimpleNamespace(enabled=True),
    )

    with patch.object(OrchestrationService, "_capture_inbound_replies", Mock()), \
         patch.object(OrchestrationService, "_sync_label_tracking", Mock()) as label_sync, \
         patch("app.services.orchestration_service.list_conversations", return_value=["row"]):
        assert service.refresh_inbox_replies(Mock()) == ["row"]

    label_sync.assert_called_once()
