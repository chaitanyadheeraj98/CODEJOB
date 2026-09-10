"""Group and relay addresses are never watched, at either level.

The bug these pin: `hstjava@googlegroups.com` was stored as an *address* watch
and survived every reconcile, because the shared-infrastructure blocklist only
gated *domain* watches. Watching a list server subscribes the dossier to a
firehose, which is the opposite of what a hand-applied label means.

The line being drawn is against freemail, which is deliberately treated more
leniently: a recruiter on a personal Gmail is a real correspondent, so a
freemail address stays watchable even though the domain never is.
"""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import RecruiterWatch, TrackedThread, UserSettings
from app.services import label_tracking_service as service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(UserSettings(owner_id="a"))
        session.flush()
        yield session
    engine.dispose()


def _thread(db, name="t", addresses=()):
    row = TrackedThread(
        owner_id="a",
        external_thread_id=name,
        label_external_ids_json=json.dumps(["Label_1"]),
        participants_json=json.dumps([{"address": a, "role": "from"} for a in addresses]),
    )
    db.add(row)
    db.flush()
    return row


def test_a_group_address_never_becomes_a_watch(db):
    thread = _thread(db, addresses=["hstjava@googlegroups.com", "naman@valzosoft.com"])

    watches = service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com")

    values = {w.value for w in watches}
    assert values == {"naman@valzosoft.com", "valzosoft.com"}
    assert not any("googlegroups" in value for value in values)


def test_a_freemail_address_is_still_watched_even_though_its_domain_is_not(db):
    """The distinction that makes `_is_infrastructure` a separate check."""
    thread = _thread(db, addresses=["charanteja4267@gmail.com"])

    values = {(w.watch_type, w.value) for w in service.derive_watches(db, "a", thread=thread, owner_email="me@outlook.com")}

    assert ("address", "charanteja4267@gmail.com") in values
    assert ("domain", "gmail.com") not in values


def test_relay_and_tracker_domains_are_refused_too(db):
    thread = _thread(db, addresses=["notify@mailsuite.com", "bounce@sendgrid.net", "x@groups.io"])

    assert service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com") == []


def test_a_subdomain_of_a_group_host_is_refused(db):
    thread = _thread(db, addresses=["digest@lists.googlegroups.com"])

    assert service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com") == []


def test_reconcile_releases_a_group_address_watch_stored_before_the_rule(db):
    """The live row that prompted this: an address watch the old code let stand."""
    thread = _thread(db, addresses=["naman@valzosoft.com"])
    service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com")
    db.add(RecruiterWatch(
        owner_id="a", watch_type="address", value="hstjava@googlegroups.com",
        source_thread_ids_json=json.dumps(["t"]), origin_label_external_id="Label_1",
    ))
    db.flush()

    released = service.reconcile_watches(db, "a")

    assert released == 1
    live = {w.value for w in db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None))}
    assert live == {"naman@valzosoft.com", "valzosoft.com"}


def test_a_stored_group_watch_never_reaches_the_gmail_query(db):
    """Defence in depth: reconcile may not have run yet when a sync fires."""
    stored = SimpleNamespace(watch_type="address", value="hstjava@googlegroups.com")

    assert service.build_watch_query([stored]) == ""


def test_the_query_still_carries_a_real_recruiter(db):
    stored = SimpleNamespace(watch_type="address", value="naman@valzosoft.com")

    query = service.build_watch_query([stored])

    assert 'from:"naman@valzosoft.com"' in query and 'bcc:"naman@valzosoft.com"' in query


def test_extra_infrastructure_domains_are_configurable(db, monkeypatch):
    monkeypatch.setattr(settings, "label_watch_extra_infrastructure_domains", "internal-relay.test")
    thread = _thread(db, addresses=["noreply@internal-relay.test"])

    assert service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com") == []
