"""Composing, not replying.

Every assistant mail path used to be anchored to a `RecruiterEmail` row: the
tool required a candidate id and the route refused without a Gmail thread. So
"write to xyz@example.com about the contract rate" had nothing to call, and the
model's only way to appear helpful was to reply to something else instead.

The transport for a fresh message already existed - orchestration has used
`send_new_email_with_attachment` all along. What was missing was a way for the
assistant to reach it behind the same propose-then-confirm boundary as
everything else, which is what these tests hold in place.

A new message has no thread to inherit trust from, so the grading basis is what
the owner deliberately recorded rather than who they have corresponded with.
That is a stricter default on purpose, and the tests say so out loud.
"""

import json
import os
import unittest
from unittest.mock import patch

os.environ["DEBUG"] = "false"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.db import Base
from app.mcp_server.tools.email_actions import propose_new_email
from app.models import PremiumNumberContact, ProductivityEvent, UserSettings
from app.services.telegram_format import email_proposal, plain_text


class ComposeHarness(unittest.TestCase):
    """Fixtures only. Both suites below inherit it; neither re-runs the other."""

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
        self.previous_actions_enabled = main.settings.feature_chat_actions_enabled
        self.previous_chat_enabled = main.settings.feature_chat_enabled
        main.settings.feature_chat_enabled = True
        main.settings.feature_chat_actions_enabled = True
        self.patch = patch("app.mcp_server.tools.email_actions.SessionLocal", self.SessionLocal)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        main.settings.feature_chat_actions_enabled = self.previous_actions_enabled
        main.settings.feature_chat_enabled = self.previous_chat_enabled
        main.app.dependency_overrides.clear()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _known_contact(self, email: str = "pat@acme-staffing.com") -> None:
        with self.SessionLocal() as db:
            db.add(
                PremiumNumberContact(
                    owner_id=main.settings.owner_id,
                    display_phone_number="214-555-1212",
                    recruiter_email=email,
                    recruiter_email_domain=email.split("@", 1)[1],
                )
            )
            db.commit()

    def _send(self, **body: object):
        calls: list[dict[str, object]] = []
        with patch.object(
            main,
            "send_new_email_with_attachment",
            side_effect=lambda **kwargs: (calls.append(kwargs), "gmail-new-1")[1],
        ):
            response = self.client.post(
                "/chat/new-email",
                json={"to": "xyz@example.com", "subject": "Contract rate", "body": "Hello.", **body},
            )
        return response, calls


class NewEmailProposalTests(ComposeHarness):
    # --- the tool --------------------------------------------------------

    def test_it_proposes_a_thread_of_its_own(self) -> None:
        proposal = propose_new_email("xyz@example.com", "Contract rate", "Can we discuss?")
        self.assertEqual(proposal["action"], "send_new_email")
        self.assertEqual(proposal["to"], "xyz@example.com")
        self.assertEqual(proposal["subject"], "Contract rate")
        # No candidate id anywhere: that is the whole difference from a reply.
        self.assertNotIn("candidate_email_id", proposal)

    def test_it_asks_rather_than_inventing_a_subject_or_a_recipient(self) -> None:
        self.assertEqual(
            propose_new_email("", "", "Hello")["missing"], ["to", "subject"]
        )
        self.assertEqual(propose_new_email("a@b.com", "Subject", " ")["missing"], ["body"])

    def test_a_malformed_address_produces_no_card(self) -> None:
        proposal = propose_new_email("xyz", "Contract rate", "Hello")
        self.assertIn("error", proposal)
        self.assertNotIn("action", proposal)

    def test_a_stranger_costs_a_confirmation(self) -> None:
        proposal = propose_new_email("xyz@example.com", "Contract rate", "Hello")
        self.assertEqual(proposal["unknown_recipients"], ["xyz@example.com"])
        self.assertTrue(proposal["requires_recipient_confirmation"])

    def test_a_saved_contact_does_not(self) -> None:
        self._known_contact()
        proposal = propose_new_email("pat@acme-staffing.com", "Following up", "Hello")
        self.assertEqual(proposal["unknown_recipients"], [])
        self.assertFalse(proposal["requires_recipient_confirmation"])

    def test_a_colleague_of_a_saved_contact_does_not_either(self) -> None:
        self._known_contact()
        proposal = propose_new_email("billing@acme-staffing.com", "Invoice", "Hello")
        self.assertEqual(proposal["unknown_recipients"], [])

    def test_a_configured_employer_cc_counts_as_known(self) -> None:
        with self.SessionLocal() as db:
            db.add(
                UserSettings(
                    owner_id=main.settings.owner_id,
                    default_employer_cc_emails="bench@myemployer.com",
                )
            )
            db.commit()
        proposal = propose_new_email("bench@myemployer.com", "Timesheet", "Hello")
        self.assertEqual(proposal["unknown_recipients"], [])

    def test_every_cc_is_graded_not_only_the_recipient(self) -> None:
        self._known_contact()
        proposal = propose_new_email(
            "pat@acme-staffing.com", "Intro", "Hello", cc="stranger@elsewhere.com"
        )
        self.assertEqual(proposal["unknown_recipients"], ["stranger@elsewhere.com"])

    # --- the route -------------------------------------------------------

    def test_the_route_refuses_a_stranger_without_an_explicit_act(self) -> None:
        response, calls = self._send()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("xyz@example.com", response.json()["detail"])
        self.assertEqual(calls, [])

    def test_the_route_sends_once_a_human_says_so(self) -> None:
        response, calls = self._send(confirm_new_recipients=True)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "xyz@example.com")
        self.assertEqual(calls[0]["subject"], "Contract rate")
        # No thread_id kwarg at all - this is a new message, not a reply that
        # lost its thread.
        self.assertNotIn("thread_id", calls[0])

    def test_a_known_recipient_needs_no_confirmation(self) -> None:
        self._known_contact()
        response, calls = self._send(to="pat@acme-staffing.com")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(calls[0]["to"], "pat@acme-staffing.com")

    def test_the_route_refuses_a_malformed_address(self) -> None:
        response, calls = self._send(to="xyz", confirm_new_recipients=True)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_a_missing_subject_never_reaches_the_mailer(self) -> None:
        response, calls = self._send(subject="", confirm_new_recipients=True)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(calls, [])

    def test_the_event_counts_recipients_without_naming_them(self) -> None:
        response, _ = self._send(cc="two@example.com", confirm_new_recipients=True)
        self.assertEqual(response.status_code, 200, response.text)
        with self.SessionLocal() as db:
            event = db.query(ProductivityEvent).one()
            self.assertEqual(event.event_type, "chat_new_email_sent")
            metadata = json.loads(event.metadata_json)
            self.assertEqual(metadata["recipient_count"], 2)
            self.assertNotIn("xyz@example.com", event.metadata_json)

    # --- the Telegram card ------------------------------------------------

    def test_the_telegram_card_says_it_starts_a_thread(self) -> None:
        rendered = email_proposal(
            json.dumps(propose_new_email("xyz@example.com", "Contract rate", "Hello.")), 21
        )
        self.assertIsNotNone(rendered)
        visible = plain_text(rendered[0])
        self.assertIn("starts a new thread", visible)
        self.assertIn("Contract rate", visible)
        self.assertIn("xyz@example.com", visible)
        self.assertEqual(rendered[1][0][0]["callback_data"], "act:prop:send:21")

    def test_a_reply_card_is_unaffected_by_the_new_branch(self) -> None:
        reply_payload = {
            "action": "send_email",
            "candidate_email_id": 42,
            "to": "recruiter@example.com",
            "cc": "",
            "subject": "Re: Role",
            "body": "Thanks.",
            "document_ids": [],
            "document_names": [],
        }
        rendered = email_proposal(json.dumps(reply_payload), 9)
        self.assertIsNotNone(rendered)
        self.assertNotIn("starts a new thread", plain_text(rendered[0]))

    def test_a_new_email_payload_without_a_subject_renders_nothing(self) -> None:
        payload = {"action": "send_new_email", "to": "a@b.com", "subject": "", "body": "Hi"}
        self.assertIsNone(email_proposal(json.dumps(payload), 1))


if __name__ == "__main__":
    unittest.main()


class ResumeAttachmentTests(ComposeHarness):
    """Attaching a resume, and saying so when it cannot be attached.

    The reported failure was a loop: the model passed a resume id in
    `document_ids`, the tool refused it as an unknown document, Telegram drew
    nothing, and the model apologised and tried the identical call again. Three
    separate gaps, and each is covered below.
    """

    def _resume(self, name: str = "Chaithanya_Java.pdf") -> int:
        from app.models import ResumeAsset

        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id=main.settings.owner_id,
                file_path=f"/data/resumes/{name}",
                file_name=name,
                mime_type="application/pdf",
                sha256="a" * 64,
                variant_label="Banking, Healthcare, Telecom, EdTech",
            )
            db.add(row)
            db.commit()
            return row.id

    def test_a_resume_is_attachable_and_named_on_the_card(self) -> None:
        resume_id = self._resume()
        proposal = propose_new_email(
            "xyz@example.com", "Application", "Hello", resume_id=resume_id
        )
        self.assertEqual(proposal["resume_id"], resume_id)
        self.assertEqual(proposal["resume_name"], "Chaithanya_Java.pdf")

    def test_an_unknown_resume_id_produces_no_card(self) -> None:
        proposal = propose_new_email("xyz@example.com", "Application", "Hello", resume_id=999)
        self.assertIn("Unknown resume id", proposal["error"])
        self.assertNotIn("action", proposal)

    def test_another_owners_resume_is_not_attachable(self) -> None:
        from app.models import ResumeAsset

        with self.SessionLocal() as db:
            row = ResumeAsset(
                owner_id="someone-else",
                file_path="/data/resumes/theirs.pdf",
                file_name="theirs.pdf",
                mime_type="application/pdf",
                sha256="b" * 64,
            )
            db.add(row)
            db.commit()
            foreign_id = row.id
        proposal = propose_new_email(
            "xyz@example.com", "Application", "Hello", resume_id=foreign_id
        )
        self.assertIn("Unknown resume id", proposal["error"])

    def test_the_document_refusal_points_at_the_right_store(self) -> None:
        # The note that breaks the retry loop: without it the model has no way
        # to learn that a resume id is not a document id.
        proposal = propose_new_email("xyz@example.com", "Application", "Hello", document_ids=[21])
        self.assertIn("Unknown document ids: 21", proposal["error"])
        self.assertIn("resume_id", proposal["note"])
        self.assertIn("list_resumes", proposal["note"])

    def test_the_route_attaches_the_resume_file(self) -> None:
        resume_id = self._resume()
        response, calls = self._send(resume_id=resume_id, confirm_new_recipients=True)
        self.assertEqual(response.status_code, 200, response.text)
        attachments = calls[0]["attachments"]
        self.assertEqual([item.display_name for item in attachments], ["Chaithanya_Java.pdf"])
        self.assertEqual(attachments[0].path, "/data/resumes/Chaithanya_Java.pdf")

    def test_the_route_refuses_a_resume_that_vanished_between_card_and_click(self) -> None:
        response, calls = self._send(resume_id=999, confirm_new_recipients=True)
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(calls, [])

    def test_telegram_says_the_card_was_refused(self) -> None:
        # The lie this closes: the model announces a card, the tool returned an
        # error, and Telegram used to render nothing at all.
        rendered = email_proposal(
            json.dumps(propose_new_email("xyz@example.com", "Application", "Hello", document_ids=[21])),
            30,
        )
        self.assertIsNotNone(rendered)
        text, keyboard = rendered
        self.assertIsNone(keyboard)
        visible = plain_text(text)
        self.assertIn("No confirmation card was created", visible)
        self.assertIn("Unknown document ids: 21", visible)

    def test_telegram_names_the_resume_among_the_attachments(self) -> None:
        resume_id = self._resume()
        rendered = email_proposal(
            json.dumps(
                propose_new_email("xyz@example.com", "Application", "Hello", resume_id=resume_id)
            ),
            31,
        )
        self.assertIn("Chaithanya_Java.pdf", plain_text(rendered[0]))
