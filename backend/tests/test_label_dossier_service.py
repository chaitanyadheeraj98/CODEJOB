"""The dossier is the whole point of the Labels workspace.

A labeled thread and the follow-ups its watches caught are stored as separate
conversations, which is right for capture and wrong for reading. These tests pin
the join: one labeled thread, one reply that arrived under a brand-new Gmail
thread id, one chronology containing both.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import (
    EmailConversation,
    EmailReplyMessage,
    GmailLabel,
    RecruiterWatch,
    TrackedThread,
    UserSettings,
)
from app.services import label_dossier_service as service

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def seeded(db):
    """One labeled thread, one watch, one follow-up in a different thread."""
    db.add(UserSettings(owner_id="a", employer_domains="myemployer.com"))
    db.add(GmailLabel(owner_id="a", external_label_id="Label_1", name="RTR Requested", is_tracked=True))
    db.add(GmailLabel(owner_id="a", external_label_id="Label_2", name="Ignored", is_tracked=False))

    labeled = EmailConversation(owner_id="a", external_thread_id="thread-1", origin="label",
        subject_snapshot="RTR for Java Developer", recruiter_email_snapshot="naman@valzosoft.com",
        last_message_at=NOW, unread_reply_count=1)
    follow_up = EmailConversation(owner_id="a", external_thread_id="thread-2", origin="watch",
        subject_snapshot="Quick question", recruiter_email_snapshot="priya@valzosoft.com",
        last_message_at=NOW + timedelta(hours=2), unread_reply_count=1)
    db.add_all([labeled, follow_up])
    db.flush()

    watch = RecruiterWatch(owner_id="a", watch_type="domain", value="valzosoft.com",
        source_thread_ids_json=json.dumps(["thread-1"]), origin_label_external_id="Label_1")
    db.add(watch)
    db.flush()

    db.add(TrackedThread(owner_id="a", external_thread_id="thread-1", conversation_id=labeled.id,
        label_external_ids_json=json.dumps(["Label_1"]), subject_snapshot="RTR for Java Developer",
        last_message_at=NOW, participants_json=json.dumps([
            {"address": "naman@valzosoft.com", "role": "from"}, {"address": "me@gmail.com", "role": "to"}])))

    db.add_all([
        EmailReplyMessage(owner_id="a", conversation_id=labeled.id, direction="inbound",
            external_message_id="m1", sender="Naman <naman@valzosoft.com>", to_header="me@gmail.com",
            cc_header="hr@myemployer.com", snippet="Sending the RTR", body="Sending the RTR",
            received_at=NOW, read_at=None),
        EmailReplyMessage(owner_id="a", conversation_id=labeled.id, direction="outbound",
            external_message_id="m2", sender="me@gmail.com", to_header="naman@valzosoft.com",
            snippet="Signed", body="Signed", received_at=NOW + timedelta(hours=1), read_at=NOW),
        EmailReplyMessage(owner_id="a", conversation_id=follow_up.id, direction="inbound",
            external_message_id="m3", sender="Priya <priya@valzosoft.com>", to_header="me@gmail.com",
            snippet="Taking over", body="Taking over from Naman", received_at=NOW + timedelta(hours=2),
            read_at=None, matched_watch_id=watch.id),
    ])
    db.flush()
    return db


def test_a_reply_in_a_separate_thread_joins_the_same_dossier(seeded):
    dossier = service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    assert [m.external_message_id if hasattr(m, "external_message_id") else m.snippet for m in dossier.messages] == [
        "Sending the RTR", "Signed", "Taking over",
    ], "strictly chronological across both Gmail threads"
    assert dossier.thread_count == 2
    assert [m.origin for m in dossier.messages] == ["label", "label", "watch"]
    assert {m.external_thread_id for m in dossier.messages} == {"thread-1", "thread-2"}


def test_both_directions_are_present(seeded):
    dossier = service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    assert [m.direction for m in dossier.messages] == ["inbound", "outbound", "inbound"]


def test_contacts_separate_the_recruiter_from_the_employer_and_the_owner(seeded):
    dossier = service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    kinds = {c.address: c.kind for c in dossier.contacts}
    assert kinds["naman@valzosoft.com"] == "recruiter"
    assert kinds["priya@valzosoft.com"] == "recruiter", "matched by the domain watch, not by an address watch"
    assert kinds["hr@myemployer.com"] == "employer"
    assert kinds["me@gmail.com"] == "self"
    # Recruiters lead the list; the employer is shown but demoted.
    assert dossier.contacts[0].kind == "recruiter"
    assert dossier.contacts[-1].kind == "self"


def test_unread_counts_the_whole_relationship(seeded):
    dossier = service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    assert dossier.unread_count == 2, "one in the labeled thread, one in the follow-up"


def test_only_tracked_labels_are_named(seeded):
    dossier = service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    assert dossier.labels == ["RTR Requested"]
    assert dossier.watches == ["domain:valzosoft.com"]


def test_an_untracked_thread_is_not_readable(seeded):
    seeded.query(TrackedThread).one().untracked_at = NOW
    seeded.flush()

    with pytest.raises(HTTPException) as caught:
        service.thread_dossier(seeded, "a", "thread-1", owner_email="me@gmail.com")

    assert caught.value.status_code == 404


def test_overview_counts_threads_not_gmail_message_snapshots(seeded):
    overview = service.label_overview(seeded, "a")

    assert [(i.name, i.thread_count, i.unread_count) for i in overview.items] == [("RTR Requested", 1, 1)]
    assert overview.tracked_thread_total == 1
    assert overview.untracked_label_count == 1, "the untracked user label is offered in the empty state"


def test_overview_orders_busiest_label_first(db, seeded):
    quiet = GmailLabel(owner_id="a", external_label_id="Label_3", name="Aaa Quiet", is_tracked=True)
    db.add(quiet)
    db.flush()

    names = [item.name for item in service.label_overview(db, "a").items]

    assert names == ["RTR Requested", "Aaa Quiet"], "count wins over the alphabet"
