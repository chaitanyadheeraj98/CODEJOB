from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.models import EmailConversation, TrackedThread, UserSettings, ResumeAsset, AppTSApplication, RecruiterEmail
from app.services import label_tracking_service
from app.models import EmailConversation, TrackedThread, UserSettings, ResumeAsset, AppTSApplication, RecruiterEmail
from app.services import label_tracking_service


def test_catalog_routes_are_owner_scoped_and_validate_ids():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    def db_dependency():
        with factory() as db:
            yield db
    main.app.dependency_overrides[main.get_db] = db_dependency
    main.orchestration_service = None
    try:
        client = TestClient(main.app)
        with patch("app.gmail_client.list_gmail_labels", return_value=[{"id": "Label_1", "name": "RTR"}]):
            response = client.post("/gmail/labels/sync")
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["name"] == "RTR"
        assert client.patch("/gmail/labels/tracked", json={"external_label_ids": ["bad"]}).status_code == 422
        assert client.patch("/gmail/labels/tracked", json={"external_label_ids": ["Label_1"]}).status_code == 200
        assert len(client.get("/gmail/labels?tracked_only=true").json()["items"]) == 1
        assert client.get("/filter-options?bucket=gmail_labels&field=label").status_code == 200
        with patch.object(main.settings, "owner_id", "other"):
            assert client.get("/gmail/labels").json()["items"] == []
        with factory() as db:
            owner = main.settings.owner_id
            user = db.query(UserSettings).filter_by(owner_id=owner).first()
            if user is None:
                user = UserSettings(owner_id=owner)
                db.add(user)
            user.enabled = user.feature_applications_enabled = user.feature_label_tracking_enabled = True
            user.signature_email = "me@gmail.com"
            resume = ResumeAsset(owner_id=owner, file_path="resume.pdf", file_name="resume.pdf", mime_type="application/pdf", sha256="sha", version=1)
            db.add(resume)
            db.flush()
            resume_id = resume.id
            from types import SimpleNamespace
            deps = SimpleNamespace(list_candidates_by_label_ids=lambda *a, **k: [dict(external_message_id="msg", external_thread_id="thread", sender="naman@valzosoft.com", subject="RTR", body="Confirm", label_ids=["Label_1", "UNREAD"])])
            label_tracking_service.sync_tracked_labels(db, owner, deps=deps, owner_email="me@gmail.com")
            db.commit()
        listing = client.get("/appts/label-threads?label=RTR&status=untracked")
        assert listing.status_code == 200, listing.text
        assert listing.json()["total"] == 1
        assert client.get("/appts/label-threads?status=invalid").status_code == 422
        assert client.get("/appts/label-threads?label=unknown").json()["total"] == 0
        assert client.get("/inbox/conversations?label=RTR&unread_only=true").json()[0]["origin"] == "label"
        assert client.get("/records/by-message/msg").json()["origin"] == "label"
        assert client.get("/records/by-message/missing").status_code == 404
        assert client.post("/appts/label-threads/thread/promote", json={"resume_asset_id": 0}).status_code == 422
        assert client.post("/appts/label-threads/thread/promote", json={"resume_asset_id": 999}).status_code == 404
        with patch("app.services.appts_service.enqueue_embedding_generation"):
            first = client.post("/appts/label-threads/thread/promote", json={"resume_asset_id": resume_id})
            second = client.post("/appts/label-threads/thread/promote", json={"resume_asset_id": resume_id})
        assert first.status_code == 201, first.text
        assert second.status_code == 200, second.text
        assert first.json()["id"] == second.json()["id"]
        assert first.json()["tracking_origin"] == "label"
        assert first.json()["resume_submission_status"] == "not_submitted"
        assert client.get("/appts/label-threads?status=promoted").json()["total"] == 1
        with factory() as db:
            assert db.query(AppTSApplication).count() == 1
            assert db.query(RecruiterEmail).count() == 0
            source = RecruiterEmail(owner_id=owner, sender="recruiter@company.com", subject="Java", body="Role", external_message_id="parsed", record_id="parsed-record")
            db.add(source)
            db.commit()
            source_id = source.id
        with patch("app.services.appts_service.enqueue_embedding_generation"):
            tracked = client.post("/appts/applications", json={"resume_asset_id": resume_id, "recruiter_email_id": source_id, "dedupe_key": "record-key"})
        assert tracked.status_code == 201, tracked.text
        assert tracked.json()["tracking_origin"] == "email"
        with factory() as db:
            assert db.get(RecruiterEmail, source_id).resume_asset_id is None
        with patch.object(main.settings, "owner_id", "other"), patch.object(main, "_require_applications_enabled"):
            assert client.get("/appts/label-threads").json()["total"] == 0
            assert client.post("/appts/label-threads/thread/promote", json={"resume_asset_id": resume_id}).status_code == 404
            assert client.get("/records/by-message/msg").status_code == 404
    finally:
        main.app.dependency_overrides.clear()
        main.orchestration_service = None
        engine.dispose()
