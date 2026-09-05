"""Attaching stored documents to a mail the assistant drafts.

The whole point of the locker is this path: "draft a reply and attach my
passport and the W2". It crosses the propose-then-confirm boundary, so the
assertions split in two - what `propose_send_email` may do (resolve ids, name
files, send nothing) and what the confirm route does with the ids it is handed.

The recurring theme is that a partial send is the wrong failure. The user
confirmed a card listing three files; a mail that arrives with two of them, or
with a different file that happened to match a name, is worse than one that does
not arrive.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.ai.chat.system_prompt import build_system_prompt
from app.db import Base
from app.mcp_server.tools.email_actions import propose_send_email
from app.models import CandidateDocument, ProductivityEvent, RecruiterEmail


class CandidateDocumentAttachTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        def override_get_db():
            with self.SessionLocal() as db:
                yield db

        main.app.dependency_overrides[main.get_db] = override_get_db
        self.client = TestClient(main.app)

        self.storage = tempfile.TemporaryDirectory()
        self.previous_storage = main.settings.candidate_document_storage_dir
        main.settings.candidate_document_storage_dir = self.storage.name
        self.previous_actions_enabled = main.settings.feature_chat_actions_enabled
        main.settings.feature_chat_actions_enabled = True

        self.patches = [
            patch("app.mcp_server.tools.email_actions.SessionLocal", self.SessionLocal),
        ]
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self) -> None:
        for active_patch in reversed(self.patches):
            active_patch.stop()
        main.settings.feature_chat_actions_enabled = self.previous_actions_enabled
        main.settings.candidate_document_storage_dir = self.previous_storage
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()
        self.storage.cleanup()

    def _candidate(self, *, thread: str | None = "thread-1") -> int:
        with self.SessionLocal() as db:
            row = RecruiterEmail(
                owner_id=main.settings.owner_id,
                sender="Recruiter <recruiter@example.com>",
                subject="Java role",
                body="Please send your passport",
                state="needs_review",
                recipient_email="to@example.com",
                cc_email="cc@example.com",
                external_thread_id=thread,
            )
            db.add(row)
            db.commit()
            return row.id

    def _document(self, name: str, label: str = "", payload: bytes = b"scan") -> int:
        response = self.client.post(
            "/settings/documents", files=[("files", (name, payload, "application/pdf"))]
        )
        self.assertEqual(response.status_code, 200, response.text)
        document_id = response.json()[0]["id"]
        if label:
            self.client.patch(f"/settings/documents/{document_id}", json={"label": label})
        return document_id

    def _send(self, email_id: int, document_ids: list[int] | None = None):
        calls: list[dict[str, object]] = []
        with patch.object(
            main,
            "send_reply_with_attachment",
            side_effect=lambda **kwargs: (calls.append(kwargs), "gmail-1")[1],
        ):
            body: dict[str, object] = {"body": "Attached, thanks"}
            if document_ids is not None:
                body["document_ids"] = document_ids
            response = self.client.post(f"/candidates/{email_id}/send-chat-reply", json=body)
        return response, calls

    # --- the proposal -----------------------------------------------------

    def test_the_proposal_names_every_file_and_sends_nothing(self) -> None:
        email_id = self._candidate()
        passport = self._document("CD_scan_0412.pdf", label="passport")
        w2 = self._document("2024_W2.pdf")

        with patch.object(main, "send_reply_with_attachment") as never_sent:
            proposal = propose_send_email(email_id, "Attached, thanks", document_ids=[passport, w2])
        never_sent.assert_not_called()

        self.assertEqual(proposal["action"], "send_email")
        self.assertEqual(proposal["document_ids"], [passport, w2])
        # The card shows real file names, not the labels: the user is confirming
        # what the recruiter will receive.
        self.assertEqual(proposal["document_names"], ["CD_scan_0412.pdf", "2024_W2.pdf"])

    def test_a_proposal_with_no_documents_still_works(self) -> None:
        email_id = self._candidate()
        proposal = propose_send_email(email_id, "Just a note")
        self.assertEqual(proposal["document_ids"], [])
        self.assertEqual(proposal["document_names"], [])

    def test_an_invented_id_fails_the_proposal_rather_than_being_dropped(self) -> None:
        """A card cannot show the absence of a file the user asked for."""
        email_id = self._candidate()
        passport = self._document("passport.pdf")
        proposal = propose_send_email(email_id, "Attached", document_ids=[passport, 4242])
        self.assertIn("4242", proposal["error"])
        self.assertNotIn("action", proposal)

    def test_another_owners_document_cannot_be_proposed(self) -> None:
        email_id = self._candidate()
        with self.SessionLocal() as db:
            theirs = CandidateDocument(
                owner_id="somebody-else",
                file_path="/tmp/theirs.pdf",
                file_name="theirs.pdf",
                sha256="0" * 64,
                file_size=1,
            )
            db.add(theirs)
            db.commit()
            theirs_id = theirs.id
        proposal = propose_send_email(email_id, "Attached", document_ids=[theirs_id])
        self.assertIn("Unknown document ids", proposal["error"])

    def test_duplicate_ids_are_collapsed(self) -> None:
        email_id = self._candidate()
        passport = self._document("passport.pdf")
        proposal = propose_send_email(email_id, "Attached", document_ids=[passport, passport])
        self.assertEqual(proposal["document_ids"], [passport])

    # --- the confirmed send -----------------------------------------------

    def test_confirming_sends_the_files_with_their_real_names(self) -> None:
        email_id = self._candidate()
        passport = self._document("CD_scan_0412.pdf", label="passport")
        w2 = self._document("2024_W2.pdf", payload=b"w2 bytes")

        response, calls = self._send(email_id, [passport, w2])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["attached_documents"], ["CD_scan_0412.pdf", "2024_W2.pdf"])

        attachments = calls[0]["attachments"]
        self.assertEqual([item.display_name for item in attachments], ["CD_scan_0412.pdf", "2024_W2.pdf"])
        self.assertEqual([item.mime_type for item in attachments], ["application/pdf", "application/pdf"])
        for item in attachments:
            self.assertTrue(Path(item.path).exists())
        self.assertEqual(Path(attachments[1].path).read_bytes(), b"w2 bytes")

    def test_a_send_with_no_documents_passes_none(self) -> None:
        """The existing no-attachment reply must keep behaving exactly as before."""
        email_id = self._candidate()
        response, calls = self._send(email_id)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(calls[0]["attachments"])
        self.assertEqual(response.json()["attached_documents"], [])

    def test_an_unknown_id_sends_nothing_at_all(self) -> None:
        email_id = self._candidate()
        response, calls = self._send(email_id, [4242])
        self.assertEqual(response.status_code, 400)
        self.assertIn("4242", response.json()["detail"])
        self.assertEqual(calls, [])

    def test_a_document_missing_from_disk_sends_nothing_at_all(self) -> None:
        """The row survives a wiped volume; the file does not.

        Sending the mail without it would look like success to the user and
        arrive incomplete to the recruiter.
        """
        email_id = self._candidate()
        passport = self._document("passport.pdf")
        with self.SessionLocal() as db:
            Path(db.get(CandidateDocument, passport).file_path).unlink()

        response, calls = self._send(email_id, [passport])
        self.assertEqual(response.status_code, 409)
        self.assertIn("passport.pdf", response.json()["detail"])
        self.assertEqual(calls, [])

    def test_a_batch_over_the_mail_ceiling_is_refused_before_sending(self) -> None:
        email_id = self._candidate()
        first = self._document("a.pdf")
        second = self._document("b.pdf")
        with self.SessionLocal() as db:
            for document_id in (first, second):
                db.get(CandidateDocument, document_id).file_size = 12 * 1024 * 1024
            db.commit()

        response, calls = self._send(email_id, [first, second])
        self.assertEqual(response.status_code, 413)
        self.assertIn("18MB", response.json()["detail"])
        self.assertEqual(calls, [])

    def test_another_owners_document_cannot_be_sent(self) -> None:
        email_id = self._candidate()
        with self.SessionLocal() as db:
            theirs = CandidateDocument(
                owner_id="somebody-else",
                file_path="/tmp/theirs.pdf",
                file_name="theirs.pdf",
                sha256="0" * 64,
                file_size=1,
            )
            db.add(theirs)
            db.commit()
            theirs_id = theirs.id
        response, calls = self._send(email_id, [theirs_id])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [])

    def test_the_attached_names_are_recorded_on_the_audit_event(self) -> None:
        email_id = self._candidate()
        passport = self._document("passport.pdf")
        self._send(email_id, [passport])
        with self.SessionLocal() as db:
            event = db.query(ProductivityEvent).filter_by(event_type="chat_reply_sent").one()
            self.assertIn("passport.pdf", event.metadata_json)

    def test_the_send_route_stays_behind_the_actions_flag(self) -> None:
        email_id = self._candidate()
        passport = self._document("passport.pdf")
        main.settings.feature_chat_actions_enabled = False
        response, calls = self._send(email_id, [passport])
        self.assertEqual(response.status_code, 404)
        self.assertEqual(calls, [])

    def test_too_many_ids_are_refused_by_the_schema(self) -> None:
        email_id = self._candidate()
        response = self.client.post(
            f"/candidates/{email_id}/send-chat-reply",
            json={"body": "Attached", "document_ids": list(range(1, 30))},
        )
        self.assertEqual(response.status_code, 422)

    # --- what the assistant is told ---------------------------------------

    def test_the_prompt_tells_the_assistant_how_to_attach(self) -> None:
        previous = main.settings.feature_chat_actions_enabled
        main.settings.feature_chat_actions_enabled = True
        try:
            prompt = build_system_prompt()
        finally:
            main.settings.feature_chat_actions_enabled = previous
        self.assertIn("list_candidate_documents", prompt)
        self.assertIn("document_ids", prompt)
        # The two failure modes worth naming: a wrong file is worse than none.
        self.assertIn("Never invent an id", prompt)
        self.assertIn("substitute a different one", prompt)

    def test_a_read_only_session_is_not_told_about_attaching(self) -> None:
        previous = main.settings.feature_chat_actions_enabled
        main.settings.feature_chat_actions_enabled = False
        try:
            prompt = build_system_prompt()
        finally:
            main.settings.feature_chat_actions_enabled = previous
        self.assertNotIn("list_candidate_documents", prompt)


if __name__ == "__main__":
    unittest.main()
