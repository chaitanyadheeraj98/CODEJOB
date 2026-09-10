import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import GmailLabel, TrackedThread, RecruiterWatch, EmailConversation, EmailReplyMessage, UserSettings
from app.config import settings
from app.services import label_tracking_service as service
from app.services.orchestration_service import OrchestrationService


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def thread(db, name="t", labels=None):
    row = TrackedThread(owner_id="a", external_thread_id=name, label_external_ids_json=json.dumps(labels or ["Label_1"]),
        participants_json=json.dumps([{"address": a, "role": r} for a, r in [
            ("me@gmail.com", "to"), ("naman@valzosoft.com", "from"), ("friend@gmail.com", "cc"), ("person@outlook.com", "bcc")]]))
    db.add(row)
    db.flush()
    return row


def test_freemail_owner_and_provenance(db, monkeypatch):
    t = thread(db)
    watches = service.derive_watches(db, "a", thread=t, owner_email="Me <me@gmail.com>")
    values = {(w.watch_type, w.value) for w in watches}
    assert ("domain", "valzosoft.com") in values
    assert ("address", "friend@gmail.com") in values
    assert all(v not in {"gmail.com", "outlook.com", "me@gmail.com"} for _, v in values)
    t2 = thread(db, "t2")
    service.derive_watches(db, "a", thread=t2, owner_email="me@gmail.com")
    assert db.query(RecruiterWatch).count() == len(values)
    assert all(json.loads(w.source_thread_ids_json) == ["t", "t2"] for w in watches)
    t.untracked_at = service.datetime.now(service.UTC)
    assert service.reconcile_watches(db, "a") == 0
    t2.untracked_at = service.datetime.now(service.UTC)
    assert service.reconcile_watches(db, "a") == len(values)
    monkeypatch.setattr(settings, "label_watch_extra_freemail_domains", "valzosoft.com")
    assert service._freemail("sub.valzosoft.com")


def test_watch_limits_and_query_injection(db, monkeypatch):
    monkeypatch.setattr(settings, "label_tracking_max_watches", 1)
    watches = service.derive_watches(db, "a", thread=thread(db), owner_email="me@gmail.com")
    assert len(watches) == 1
    query = service.build_watch_query(watches)
    assert 'bcc:"' in query and "newer_than:45d" in query
    malicious = SimpleNamespace(watch_type="domain", value='gmail.com) OR in:anywhere (')
    assert service.build_watch_query([malicious]) == ""
    assert service.build_watch_query([SimpleNamespace(watch_type="domain", value="gmail.com")]) == ""
    with pytest.raises(ValueError):
        service.build_watch_query(watches * 21)


def test_reconcile_removal_multiple_labels_and_incomplete_scan(db):
    for id in ("Label_1", "Label_2"):
        db.add(GmailLabel(owner_id="a", external_label_id=id, name=id, is_tracked=True))
    t = thread(db, labels=["Label_1", "Label_2"])
    service.derive_watches(db, "a", thread=t, owner_email="me@gmail.com")
    live = {"Label_1": set(), "Label_2": {"t"}}
    deps = SimpleNamespace(list_thread_ids_by_label=lambda id: live[id])
    assert service.reconcile_untracked(db, "a", deps=deps) == 0
    assert json.loads(t.label_external_ids_json) == ["Label_2"]
    failing = SimpleNamespace(list_thread_ids_by_label=Mock(side_effect=RuntimeError("incomplete")))
    with pytest.raises(RuntimeError):
        service.reconcile_untracked(db, "a", deps=failing)
    assert t.untracked_at is None
    live["Label_2"] = set()
    assert service.reconcile_untracked(db, "a", deps=deps) == 1
    assert db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None)).count() == 0


def test_relabel_stored_messages_and_new_thread_watch(db):
    db.add(GmailLabel(owner_id="a", external_label_id="Label_1", name="RTR", is_tracked=True))
    item = dict(external_message_id="m", external_thread_id="t", sender="naman@valzosoft.com", subject="RTR", body="confirm", label_ids=["UNREAD"])
    deps = SimpleNamespace(list_candidates_by_label_ids=Mock(return_value=[item]), list_thread_ids_by_label=Mock(return_value={"t"}))
    service.sync_tracked_labels(db, "a", deps=deps, owner_email="me@gmail.com")
    service.release_label(db, "a", "Label_1")
    assert db.query(TrackedThread).one().untracked_at
    service.reconcile_untracked(db, "a", deps=deps)
    assert db.query(TrackedThread).one().untracked_at is None
    deps.list_candidates_by_query = Mock(return_value=[{**item, "external_message_id": "new", "external_thread_id": "new-thread", "sender": "other@valzosoft.com"}])
    assert service.sync_watch_matches(db, "a", deps=deps, owner_email="me@gmail.com").messages == 1
    conv = db.query(EmailConversation).filter_by(external_thread_id="new-thread").one()
    assert conv.origin == "watch"
    assert db.query(EmailReplyMessage).filter_by(external_message_id="new").one().matched_watch_id
    assert service.sync_watch_matches(db, "a", deps=deps, owner_email="me@gmail.com").messages == 0
    deps.list_thread_ids_by_label.return_value = set()
    service.reconcile_untracked(db, "a", deps=deps)
    deps.list_candidates_by_query.reset_mock()
    assert service.sync_watch_matches(db, "a", deps=deps, owner_email="me@gmail.com").messages == 0
    deps.list_candidates_by_query.assert_not_called()


def test_label_membership_pagination_never_falsely_untracks():
    from app import gmail_client
    gmail = Mock()
    gmail.users().messages().list().execute.return_value = {"messages": [{"threadId": "t"}], "nextPageToken": "more"}
    with patch.object(gmail_client, "_gmail_service", return_value=gmail), pytest.raises(RuntimeError):
        gmail_client.list_thread_ids_by_label("Label_1")


def test_gmail_label_fetch_skips_known_full_gets():
    from app import gmail_client
    gmail = Mock()
    gmail.users().messages().list().execute.return_value = {"messages": [{"id": "known"}, {"id": "new"}]}
    gmail.users().messages().get().execute.return_value = {"id": "new", "threadId": "t", "payload": {"headers": []}}
    gmail.users().messages().get.reset_mock()
    with patch.object(gmail_client, "_gmail_service", return_value=gmail):
        gmail_client.list_candidates_by_label_ids(["Label_1"], skip_message_ids={"known"})
    gmail.users().messages().get.assert_called_once_with(userId="me", id="new", format="full")


def test_orchestrator_dark_gate_and_failure_isolation(db, monkeypatch):
    deps = SimpleNamespace(owner_id="a", list_gmail_labels=lambda: [], list_candidates_by_label_ids=Mock(return_value=[]),
        list_thread_ids_by_label=Mock(return_value=set()), list_candidates_by_query=Mock(return_value=[]))
    pipeline = OrchestrationService(deps)
    user = UserSettings(owner_id="a", feature_label_tracking_enabled=True, signature_email="me@gmail.com")
    monkeypatch.setattr(settings, "feature_label_tracking_enabled", False)
    assert pipeline._sync_label_tracking(db, user) == (0, 0, 0)
    deps.list_candidates_by_label_ids.assert_not_called()
    monkeypatch.setattr(settings, "feature_label_tracking_enabled", True)
    with patch.object(pipeline, "sync_gmail_labels", side_effect=RuntimeError("offline")):
        assert pipeline._sync_label_tracking(db, user)[2] == 1
    deps.list_candidates_by_query.assert_not_called()


def test_shared_infrastructure_domains_never_become_domain_watches():
    """A live sync derived a domain watch on googlegroups.com. This app is fed
    by Google Groups, so that watch would follow the bulk requirement firehose
    instead of one recruiter's company - and mailsuite.com is a tracking pixel,
    not an employer."""
    from app.services.label_tracking_service import _freemail

    for domain in ("googlegroups.com", "mailsuite.com", "sendgrid.net", "groups.io", "bounces.google.com"):
        assert _freemail(domain), domain
    # Subdomains too: a list server rarely sends from the apex.
    assert _freemail("lists.googlegroups.com")
    # Real employers must still be watchable, including the user's own example.
    for domain in ("valzosoft.com", "horizonsoftech.net", "rpatechnologyinc.com"):
        assert not _freemail(domain), domain


def test_reconcile_releases_a_domain_watch_the_blocklist_now_covers():
    """A blocklist entry added after a watch was derived must still reach it."""
    from datetime import UTC, datetime

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.db import Base
    from app.models import RecruiterWatch, TrackedThread
    from app.services.label_tracking_service import reconcile_watches

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(TrackedThread(owner_id="a", external_thread_id="t1", label_external_ids_json='["Label_1"]'))
        db.add(RecruiterWatch(owner_id="a", watch_type="domain", value="googlegroups.com", source_thread_ids_json='["t1"]'))
        db.add(RecruiterWatch(owner_id="a", watch_type="domain", value="valzosoft.com", source_thread_ids_json='["t1"]'))
        # An address at a blocked domain is still a real person and stays.
        db.add(RecruiterWatch(owner_id="a", watch_type="address", value="naman@gmail.com", source_thread_ids_json='["t1"]'))
        db.flush()

        assert reconcile_watches(db, "a") == 1
        db.flush()
        live = {w.value for w in db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None))}
        assert live == {"valzosoft.com", "naman@gmail.com"}
    engine.dispose()
