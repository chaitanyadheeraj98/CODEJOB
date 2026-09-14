import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import Base
from app.models import AppTSApplication, RecruiterWatch, ResumeAsset, UserSettings
from app.services import application_service, appts_service


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def add_settings(db, *, enabled=True, employer_domains=""):
    row = UserSettings(
        owner_id="a",
        feature_application_watches_enabled=enabled,
        employer_domains=employer_domains,
    )
    db.add(row)
    db.flush()
    return row


def add_application(db, email="recruiter@example.com", status="matched"):
    row = AppTSApplication(
        owner_id="a",
        resume_asset_id=1,
        resume_version_snapshot=1,
        resume_file_name_snapshot="resume.pdf",
        resume_sha256_snapshot="sha",
        manual_recruiter_email=email,
        resolved_recruiter_email=email.strip().lower() or None,
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def test_flag_off_derives_nothing(db):
    add_settings(db, enabled=False)
    application = add_application(db)

    assert appts_service.derive_application_watches(db, "a", application) == []
    assert db.query(RecruiterWatch).count() == 0


def test_manual_application_derives_address_and_corporate_domain(db):
    add_settings(db)
    resume = ResumeAsset(
        owner_id="a", file_path="resume.pdf", file_name="resume.pdf",
        mime_type="application/pdf", sha256="sha", version=1,
    )
    db.add(resume)
    db.flush()

    application, created = appts_service.create_tracked_application_manual(
        db,
        owner_id="a",
        resume_asset_id=resume.id,
        dedupe_key="manual-1",
        manual_recruiter_name="Recruiter",
        manual_recruiter_company="Example",
        manual_job_title="Engineer",
        manual_recruiter_email="Recruiter@Example.com",
    )

    assert created
    assert {(watch.watch_type, watch.value) for watch in db.query(RecruiterWatch)} == {
        ("address", "recruiter@example.com"),
        ("domain", "example.com"),
    }
    assert all(watch.source_application_ids_json == f"[{application.id}]" for watch in db.query(RecruiterWatch))


def test_freemail_derives_address_only(db):
    add_settings(db)
    application = add_application(db, "recruiter@gmail.com")

    watches = appts_service.derive_application_watches(db, "a", application)

    assert {(watch.watch_type, watch.value) for watch in watches} == {("address", "recruiter@gmail.com")}


def test_employer_domain_derives_nothing(db):
    add_settings(db, employer_domains="example.com")
    application = add_application(db)

    assert appts_service.derive_application_watches(db, "a", application) == []


def test_terminal_status_releases_and_reactivation_unreleases(db):
    add_settings(db)
    application = add_application(db)
    appts_service.derive_application_watches(db, "a", application)

    application_service.update_status(
        db,
        application,
        new_status="rejected",
        models=appts_service.APPTS_MODELS,
    )
    assert db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None)).count() == 0

    application_service.update_status(
        db,
        application,
        new_status="matched",
        models=appts_service.APPTS_MODELS,
    )
    assert db.query(RecruiterWatch).filter(RecruiterWatch.released_at.is_(None)).count() == 2


def test_budget_refuses_at_cap_without_logging_the_watch_value(db, monkeypatch, caplog):
    add_settings(db)
    application = add_application(db)
    monkeypatch.setattr(settings, "label_tracking_max_watches", 1)

    with caplog.at_level(logging.WARNING):
        watches = appts_service.derive_application_watches(db, "a", application)

    assert len(watches) == 1
    assert "label_tracking_watch_limit_reached application_id=" in caplog.text
    assert "recruiter@example.com" not in caplog.text
