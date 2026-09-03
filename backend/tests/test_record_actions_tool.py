import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.db import Base
from app.mcp_server.tools.record_actions import UPDATABLE, propose_add_note, propose_record_update
from app.models import (
    APPLICATION_STATUS_VALUES,
    Application,
    PremiumNumberContact,
    RecruiterOpportunity,
    ResumeAsset,
    UserSettings,
)

OTHER_OWNER = "someone-else"


class RecordActionToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        self.patch = patch("app.mcp_server.tools.record_actions.SessionLocal", self.SessionLocal)
        self.patch.start()
        self.addCleanup(self.patch.stop)

        with self.SessionLocal() as db:
            db.add(UserSettings(owner_id=settings.owner_id, gmail_query="", default_gmail_query=""))
            db.add(ResumeAsset(
                id=1, owner_id=settings.owner_id, file_path="r.pdf", file_name="java.pdf",
                sha256="a" * 64, version=1,
            ))
            db.add(PremiumNumberContact(
                id=1, owner_id=settings.owner_id, is_recruiter=True,
                normalized_phone_number="12145551212", display_phone_number="+1 214 555 1212",
                recruiter_name="Sarah", company="Acme",
            ))
            db.add(PremiumNumberContact(
                id=2, owner_id=OTHER_OWNER, is_recruiter=True,
                normalized_phone_number="12145551213", display_phone_number="+1 214 555 1213",
                recruiter_name="Theirs", company="Other",
            ))
            db.add(RecruiterOpportunity(
                id=1, owner_id=settings.owner_id, recruiter_number_id=1, gmail_message_id="m1",
                job_title="Java Developer", status="New", location="Dallas, TX",
                notes="Called on Tuesday.",
            ))
            db.add(RecruiterOpportunity(
                id=2, owner_id=OTHER_OWNER, recruiter_number_id=2, gmail_message_id="m2",
                job_title="Their Role", status="New",
            ))
            db.add(Application(
                id=1, owner_id=settings.owner_id, resume_asset_id=1, resume_version_snapshot=1,
                resume_file_name_snapshot="java.pdf", resume_sha256_snapshot="b" * 64,
                recruiter_opportunity_id=1, recruiter_contact_id=1,
                status="contacted", dedupe_key="app-1",
            ))
            db.commit()

    def snapshot(self) -> tuple:
        with self.SessionLocal() as db:
            opp = db.get(RecruiterOpportunity, 1)
            app_row = db.get(Application, 1)
            contact = db.get(PremiumNumberContact, 1)
            return (opp.status, opp.job_title, opp.notes, app_row.status, contact.is_favorite)

    def test_a_proposal_mutates_nothing(self) -> None:
        before = self.snapshot()

        propose_record_update("opportunity", 1, {"status": "Applied", "job_title": "Senior Java"})
        propose_record_update("application", 1, {"status": "interview_1"})
        propose_record_update("contact", 1, {"is_favorite": True})
        propose_add_note("opportunity", 1, "Left a voicemail.")
        propose_add_note("application", 1, "Recruiter called back.")

        self.assertEqual(self.snapshot(), before)

    def test_the_card_shows_from_to_read_from_the_database(self) -> None:
        payload = propose_record_update("opportunity", 1, {"status": "Applied", "location": "Austin, TX"})

        changes = {change["field"]: change for change in payload["changes"]}
        self.assertEqual(changes["status"]["from"], "New")
        self.assertEqual(changes["status"]["to"], "Applied")
        self.assertEqual(changes["location"]["from"], "Dallas, TX")
        self.assertEqual(payload["record_label"], "Java Developer")
        self.assertEqual(payload["endpoint"], "/recruiter-opportunities/1")

    def test_a_field_outside_the_allowlist_is_refused_and_named(self) -> None:
        # evidence and job_confidence are extraction outputs; a model writing
        # them launders a guess in as though a parser had found it.
        for field in ("evidence", "extracted_skills", "job_confidence", "end_client_confirmed"):
            with self.subTest(field=field):
                payload = propose_record_update("opportunity", 1, {field: "anything"})
                self.assertIn(field, payload["error"])
                self.assertNotIn("action", payload)

    def test_notes_are_refused_here_and_point_at_the_note_tool(self) -> None:
        payload = propose_record_update("opportunity", 1, {"notes": "overwrite everything"})

        self.assertIn("notes", payload["error"])
        self.assertIn("propose_add_note", payload["note"])

    def test_invalid_status_returns_the_valid_list_not_a_guess(self) -> None:
        opportunity = propose_record_update("opportunity", 1, {"status": "Aplied"})
        self.assertEqual(sorted(opportunity["statuses"]), sorted({"New", "Called", "Applied", "Follow Up", "Closed", "Not Interested"}))

        application = propose_record_update("application", 1, {"status": "interviewing"})
        self.assertEqual(application["statuses"], list(APPLICATION_STATUS_VALUES))

    def test_cross_owner_record_is_not_found(self) -> None:
        self.assertIn("not found", propose_record_update("opportunity", 2, {"status": "Applied"})["error"])
        self.assertIn("not found", propose_record_update("contact", 2, {"is_favorite": True})["error"])

    def test_unknown_kind_and_empty_fields(self) -> None:
        self.assertEqual(propose_record_update("planet", 1, {"status": "New"})["kinds"], sorted(UPDATABLE))
        self.assertEqual(propose_record_update("opportunity", 1, {})["status"], "missing_fields")

    def test_opportunity_note_shows_existing_and_combined(self) -> None:
        # The endpoint replaces the column, so the honesty is in the card.
        payload = propose_add_note("opportunity", 1, "Left a voicemail.")

        self.assertIs(payload["replaces"], True)
        self.assertEqual(payload["existing_notes"], "Called on Tuesday.")
        self.assertEqual(payload["combined_notes"], "Called on Tuesday.\n\nLeft a voicemail.")
        self.assertIs(payload["append_only"], False)

    def test_application_note_is_append_only(self) -> None:
        payload = propose_add_note("application", 1, "Recruiter called back.")

        self.assertIs(payload["append_only"], True)
        self.assertEqual(payload["event_type"], "note")
        self.assertEqual(payload["endpoint"], "/applications/1/events")
        self.assertNotIn("combined_notes", payload)

    def test_note_is_marked_as_assistant_authored(self) -> None:
        self.assertEqual(propose_add_note("application", 1, "x")["authored_by"], "assistant")

    def test_empty_note_is_missing_fields(self) -> None:
        payload = propose_add_note("opportunity", 1, "   ")

        self.assertEqual(payload["status"], "missing_fields")
        self.assertEqual(payload["missing"], ["note"])

    def test_note_on_a_contact_is_refused(self) -> None:
        self.assertEqual(propose_add_note("contact", 1, "x")["kinds"], ["opportunity", "application"])


if __name__ == "__main__":
    unittest.main()
