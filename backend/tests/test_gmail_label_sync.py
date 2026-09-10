import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.models import GmailLabel, TrackedThread, RecruiterWatch
from app.services import gmail_label_service as service


def test_catalog_preserves_intent_and_releases_removed_labels():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        labels = [{"id": "Label_1", "name": "RTR Requested"}]
        assert service.sync_labels(db, "a", list_labels=lambda: labels).created == 1
        service.set_tracked(db, "a", ["Label_1"])
        labels[0]["name"] = "Interview"
        service.sync_labels(db, "a", list_labels=lambda: labels)
        assert service.list_labels(db, "a")[0].is_tracked
        assert service.resolve_label_ids(db, "a", ["Interview"]) == ["Label_1"]
        assert service.resolve_label_ids(db, "b", ["Interview"]) == []
        db.add(TrackedThread(owner_id="a", external_thread_id="t", label_external_ids_json='["Label_1"]'))
        db.add(RecruiterWatch(owner_id="a", watch_type="address", value="a@example.com", source_thread_ids_json='["t"]'))
        db.flush()
        assert service.sync_labels(db, "a", list_labels=lambda: []).tombstoned == 1
        assert service.list_labels(db, "a") == []
        assert db.query(TrackedThread).one().untracked_at
        assert db.query(RecruiterWatch).one().released_at
        assert db.query(GmailLabel).one().is_tracked
        service.sync_labels(db, "a", list_labels=lambda: labels)
        assert service.list_labels(db, "a")[0].is_tracked
        with pytest.raises(HTTPException):
            service.set_tracked(db, "b", ["Label_1"])
        service.set_tracked(db, "a", [])
        assert not service.list_labels(db, "a")[0].is_tracked
    engine.dispose()

def test_system_labels_cannot_be_tracked():
    """A label means the user filed this thread deliberately; SENT means nothing
    of the kind. Tracking one would pull the whole mailbox into the inbox tables
    and mint a recruiter watch per participant."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        labels = [
            {"id": "Label_1", "name": "RTR Requested", "type": "user"},
            {"id": "SENT", "name": "SENT", "type": "system"},
            {"id": "CATEGORY_PROMOTIONS", "name": "CATEGORY_PROMOTIONS", "type": "system"},
        ]
        service.sync_labels(db, "a", list_labels=lambda: labels)

        with pytest.raises(HTTPException) as caught:
            service.set_tracked(db, "a", ["SENT"])
        assert caught.value.status_code == 422
        assert "SENT" in caught.value.detail

        # Named, so the user learns which of a batch was refused.
        with pytest.raises(HTTPException) as caught:
            service.set_tracked(db, "a", ["Label_1", "CATEGORY_PROMOTIONS"])
        assert "CATEGORY_PROMOTIONS" in caught.value.detail

        # The whole call is refused, not partially applied.
        assert [row.external_label_id for row in service.list_labels(db, "a", tracked_only=True)] == []
        service.set_tracked(db, "a", ["Label_1"])
        assert [row.external_label_id for row in service.list_labels(db, "a", tracked_only=True)] == ["Label_1"]
    engine.dispose()


def test_a_label_without_a_type_is_classified_by_its_id():
    """Gmail omits `type` on some rows; the id prefix is the fallback, and it
    must not classify a system label as trackable."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        service.sync_labels(db, "a", list_labels=lambda: [
            {"id": "Label_9", "name": "Submissions"},
            {"id": "TRASH", "name": "TRASH"},
        ])
        by_id = {row.external_label_id: row.label_type for row in service.list_labels(db, "a")}
        assert by_id == {"Label_9": "user", "TRASH": "system"}
        with pytest.raises(HTTPException):
            service.set_tracked(db, "a", ["TRASH"])
    engine.dispose()
