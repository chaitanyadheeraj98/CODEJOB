"""Employer participants must never become recruiter watches.

The owner's employer routinely sits on the CC line of a labeled RTR thread, so
the participant list cannot be trusted as "these are all recruiters". Settings
already knows the employer domains; these tests pin that the watch derivation
consults it, and that editing the setting later retires watches it already
produced.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import AppTSApplication, RecruiterWatch, TrackedThread, UserSettings
from app.services import label_tracking_service as service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _settings(db, employer_domains=""):
    db.add(UserSettings(
        owner_id="a",
        employer_domains=employer_domains,
        feature_application_watches_enabled=True,
    ))
    db.flush()


def _thread(db, name="t", addresses=None):
    addresses = addresses or [
        ("me@gmail.com", "to"),
        ("naman@valzosoft.com", "from"),
        ("hr@myemployer.com", "cc"),
    ]
    row = TrackedThread(
        owner_id="a",
        external_thread_id=name,
        label_external_ids_json=json.dumps(["Label_1"]),
        participants_json=json.dumps([{"address": a, "role": r} for a, r in addresses]),
    )
    db.add(row)
    db.flush()
    return row


def test_employer_participants_produce_no_watch_at_all(db):
    _settings(db, "myemployer.com")

    watches = service.derive_watches(db, "a", thread=_thread(db), owner_email="me@gmail.com")

    values = {(w.watch_type, w.value) for w in watches}
    assert ("domain", "valzosoft.com") in values, "the recruiter still has to be watched"
    assert ("address", "naman@valzosoft.com") in values
    # The address is dropped too, not only the domain: a dossier of the
    # recruiter relationship has no use for internal employer mail.
    assert not any("myemployer.com" in value for _, value in values)


def test_subdomains_of_an_employer_are_excluded(db):
    _settings(db, "myemployer.com")

    watches = service.derive_watches(
        db, "a", thread=_thread(db, addresses=[("payroll@mail.myemployer.com", "from")]), owner_email="me@gmail.com"
    )

    assert watches == []


def test_a_recruiter_domain_is_untouched_when_no_employer_is_configured(db):
    _settings(db, "")

    watches = service.derive_watches(db, "a", thread=_thread(db), owner_email="me@gmail.com")

    assert ("domain", "myemployer.com") in {(w.watch_type, w.value) for w in watches}


def test_adding_an_employer_domain_releases_the_watches_it_already_made(db):
    _settings(db, "")
    thread = _thread(db)
    service.derive_watches(db, "a", thread=thread, owner_email="me@gmail.com")
    assert db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None)).count() == 4

    db.query(UserSettings).filter(UserSettings.owner_id == "a").one().employer_domains = "myemployer.com"
    db.flush()
    released = service.reconcile_watches(db, "a")

    assert released == 2, "the employer address and domain, and nothing else"
    live = {w.value for w in db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None))}
    assert live == {"naman@valzosoft.com", "valzosoft.com"}


def test_watch_with_both_sources_survives_losing_the_thread(db):
    _settings(db)
    thread = _thread(db)
    application = AppTSApplication(
        owner_id="a", resume_asset_id=1, resume_version_snapshot=1,
        resume_file_name_snapshot="resume.pdf", resume_sha256_snapshot="sha",
    )
    db.add(application)
    db.flush()
    watch = RecruiterWatch(
        owner_id="a", watch_type="address", value="naman@valzosoft.com",
        source_thread_ids_json=json.dumps([thread.external_thread_id]),
        source_application_ids_json=json.dumps([application.id]),
    )
    db.add(watch)
    db.flush()

    thread.untracked_at = service.datetime.now(service.UTC)
    assert service.reconcile_watches(db, "a") == 0
    assert json.loads(watch.source_thread_ids_json) == []
    assert json.loads(watch.source_application_ids_json) == [application.id]
    assert watch.released_at is None
